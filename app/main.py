import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator
import logging
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.image_scan import router as image_scan_router
from app.api.music_artifacts import MusicArtifactNotFound
from app.api.music_artifacts import router as music_artifact_router
from app.config import Settings
from app.errors import ApiProblem
from app.logging_utils import sanitize_log_text
from app.services.artifact_store import ArtifactStore
from app.services.ffprobe_metadata import FfprobeMetadataReader
from app.services.music_artifact_service import (
    MusicArtifactService,
    MusicArtifactTooLarge,
    MusicCapacityExceeded,
    MusicFetchTimeout,
    MusicProviderFailed,
)
from app.services.device import select_device
from app.services.nsfw_checker import NsfwChecker
from app.services.process_runner import ProcessRunner
from app.services.spotiflac_downloader import SpotiFlacDownloader


logger = logging.getLogger(__name__)
_ERROR_LOG_MAX_BYTES = 4096


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app(settings: Settings, services: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.services = services
        if getattr(services, "nsfw_checker", False) is None:
            await _initialize_runtime_services(settings, services)
        managed_runtime = getattr(services, "_managed_runtime", False)
        cleanup_task = None
        if managed_runtime:
            services.ready = True
            cleanup_task = asyncio.create_task(
                _sweep_expired_artifacts(
                    services.artifact_store,
                    settings.music_cleanup_interval_seconds,
                )
            )
        try:
            async with _lifespan(app):
                yield
        finally:
            if managed_runtime:
                services.ready = False
            if cleanup_task is not None:
                cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup_task

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def attach_request_id(request, call_next):
        request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error(
                "Unhandled request failure request_id=%s",
                request.state.request_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            response = _problem_response(
                request,
                500,
                "INTERNAL_ERROR",
                "An unexpected internal error occurred",
            )
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request_handler(request, error):
        if request.url.path == "/api/v1/music/artifacts":
            return _problem_response(
                request,
                400,
                "MUSIC_TRACK_ID_INVALID",
                "Track ID must contain 22 base-62 characters",
            )
        if request.url.path.startswith("/api/v1/music/artifacts/"):
            return _problem_response(
                request,
                404,
                "MUSIC_ARTIFACT_NOT_FOUND",
                "Music artifact was not found or has expired",
            )
        return _problem_response(
            request, 400, "REQUEST_INVALID", "Request validation failed"
        )

    @app.exception_handler(MusicArtifactNotFound)
    async def artifact_not_found_handler(request, error):
        return _problem_response(
            request,
            404,
            "MUSIC_ARTIFACT_NOT_FOUND",
            "Music artifact was not found or has expired",
        )

    @app.exception_handler(MusicCapacityExceeded)
    async def capacity_handler(request, error):
        return _problem_response(
            request,
            429,
            "MUSIC_FETCH_CAPACITY_EXCEEDED",
            "No music download slot is currently available",
        )

    @app.exception_handler(MusicArtifactTooLarge)
    async def too_large_handler(request, error):
        return _problem_response(
            request,
            413,
            "MUSIC_ARTIFACT_TOO_LARGE",
            "Music artifact exceeds the configured size limit",
        )

    async def provider_handler(request, error):
        cause = error.__cause__ or error
        logger.error(
            "music_provider_failed request_id=%s error_type=%s detail=%r",
            request.state.request_id,
            type(cause).__name__,
            sanitize_log_text(str(error), _ERROR_LOG_MAX_BYTES),
        )
        return _problem_response(
            request,
            502,
            "MUSIC_PROVIDER_FAILED",
            "No configured provider produced an audio file",
        )

    app.add_exception_handler(MusicProviderFailed, provider_handler)

    @app.exception_handler(MusicFetchTimeout)
    async def timeout_handler(request, error):
        return _problem_response(
            request,
            504,
            "MUSIC_FETCH_TIMEOUT",
            "Music artifact generation timed out",
        )

    app.include_router(health_router)
    app.include_router(image_scan_router)
    app.include_router(music_artifact_router)
    return app


def create_uninitialized_services() -> SimpleNamespace:
    return SimpleNamespace(
        ready=False,
        device="uninitialized",
        nsfw_checker=None,
    )


async def _initialize_runtime_services(settings: Settings, services: Any) -> None:
    services.ready = False
    device = select_device(settings.compute_device)
    nsfw_checker = await asyncio.to_thread(NsfwChecker.load, settings, device)
    artifact_store = await asyncio.to_thread(
        ArtifactStore,
        settings.artifact_root,
        settings.music_artifact_max_size,
        settings.music_artifact_ttl_seconds,
        lambda: datetime.now(UTC),
    )
    await artifact_store.remove_stale_directories()
    process_runner = ProcessRunner()
    downloader = SpotiFlacDownloader(settings, process_runner)
    metadata_reader = FfprobeMetadataReader(settings, process_runner)
    music_artifact_service = MusicArtifactService(
        settings,
        downloader,
        metadata_reader,
        artifact_store,
    )

    services.device = device
    services.nsfw_checker = nsfw_checker
    services.artifact_store = artifact_store
    services.music_artifact_service = music_artifact_service
    services._managed_runtime = True
    services.ready = True


async def _sweep_expired_artifacts(
    artifact_store: ArtifactStore, interval_seconds: int
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await artifact_store.sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to sweep expired music artifacts")


def _problem_response(
    request, status_code: int, code: str, message: str
) -> JSONResponse:
    problem = ApiProblem(
        code=code,
        message=message,
        request_id=request.state.request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content={"error": problem.model_dump(by_alias=True)},
    )

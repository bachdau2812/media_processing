from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.config import Settings
from app.logging_utils import sanitize_log_text
from app.models.music_artifact import MusicArtifactResponse, MusicMetadata


logger = logging.getLogger(__name__)
_ERROR_LOG_MAX_BYTES = 4096


class DownloaderLike(Protocol):
    async def download(self, track_id: str, job_dir: Path) -> Path: ...


class MetadataReaderLike(Protocol):
    async def read(self, path: Path) -> MusicMetadata: ...


class ArtifactStoreLike(Protocol):
    async def register(
        self, track_id: str, file_path: Path, metadata: MusicMetadata
    ) -> MusicArtifactResponse: ...


class MusicCapacityExceeded(Exception):
    pass


class MusicProviderFailed(RuntimeError):
    pass


class MusicArtifactTooLarge(ValueError):
    pass


class MusicFetchTimeout(TimeoutError):
    pass


class MusicArtifactService:
    def __init__(
        self,
        settings: Settings,
        downloader: DownloaderLike,
        metadata_reader: MetadataReaderLike,
        artifact_store: ArtifactStoreLike,
    ) -> None:
        configured_root = Path(settings.artifact_root)
        configured_root.mkdir(parents=True, exist_ok=True)
        self._root = configured_root.resolve(strict=True)
        self._downloader = downloader
        self._metadata_reader = metadata_reader
        self._artifact_store = artifact_store
        self._capacity_wait_seconds = float(
            settings.music_capacity_wait_seconds
        )
        self._capacity = asyncio.Semaphore(
            settings.music_max_concurrent_downloads
        )

    async def create(
        self,
        track_id: str,
        request_id: str,
    ) -> MusicArtifactResponse:
        started_at = time.monotonic()
        stage = "capacity_wait"
        job_id = "unassigned"
        logger.info(
            "music_fetch_started request_id=%s track_id=%s",
            request_id,
            track_id,
        )
        try:
            await asyncio.wait_for(
                self._capacity.acquire(),
                timeout=self._capacity_wait_seconds,
            )
        except TimeoutError as error:
            logger.warning(
                "music_capacity_exceeded request_id=%s track_id=%s "
                "elapsed_ms=%s",
                request_id,
                track_id,
                _elapsed_ms(started_at),
            )
            raise MusicCapacityExceeded from error

        logger.info(
            "music_capacity_acquired request_id=%s track_id=%s",
            request_id,
            track_id,
        )
        try:
            job_directory = self._root / str(uuid4())
            job_id = job_directory.name
            logger.info(
                "music_job_created request_id=%s track_id=%s job_id=%s",
                request_id,
                track_id,
                job_id,
            )
            try:
                stage = "download"
                logger.info(
                    "music_download_started request_id=%s track_id=%s "
                    "job_id=%s",
                    request_id,
                    track_id,
                    job_id,
                )
                audio_path = await self._downloader.download(
                    track_id, job_directory
                )
                audio_size = await asyncio.to_thread(
                    _file_size, audio_path
                )
                logger.info(
                    "music_download_completed request_id=%s track_id=%s "
                    "job_id=%s filename=%r size_bytes=%s",
                    request_id,
                    track_id,
                    job_id,
                    audio_path.name,
                    audio_size,
                )
                stage = "metadata"
                logger.info(
                    "music_metadata_started request_id=%s track_id=%s "
                    "job_id=%s",
                    request_id,
                    track_id,
                    job_id,
                )
                metadata = await self._metadata_reader.read(audio_path)
                logger.info(
                    "music_metadata_completed request_id=%s track_id=%s "
                    "job_id=%s",
                    request_id,
                    track_id,
                    job_id,
                )
                stage = "registration"
                response = await self._artifact_store.register(
                    track_id, audio_path, metadata
                )
                logger.info(
                    "music_artifact_registered request_id=%s track_id=%s "
                    "job_id=%s artifact_id=%s size_bytes=%s elapsed_ms=%s",
                    request_id,
                    track_id,
                    job_id,
                    response.artifact_id,
                    response.size_bytes,
                    _elapsed_ms(started_at),
                )
                return response
            except BaseException as error:
                try:
                    await asyncio.to_thread(
                        _remove_guarded_uuid_directory,
                        self._root,
                        job_directory,
                    )
                except Exception:
                    logger.exception(
                        "Failed to clean unregistered music job %s",
                        job_id,
                    )
                logger.warning(
                    "music_fetch_failed request_id=%s track_id=%s job_id=%s "
                    "stage=%s error_type=%s elapsed_ms=%s detail=%r",
                    request_id,
                    track_id,
                    job_id,
                    stage,
                    type(error).__name__,
                    _elapsed_ms(started_at),
                    sanitize_log_text(str(error), _ERROR_LOG_MAX_BYTES),
                )
                if isinstance(error, TimeoutError):
                    raise MusicFetchTimeout(str(error)) from error
                if isinstance(error, ValueError) and _is_too_large(error):
                    raise MusicArtifactTooLarge(str(error)) from error
                if isinstance(error, (RuntimeError, ValueError)):
                    raise MusicProviderFailed(str(error)) from error
                raise
        finally:
            self._capacity.release()


def _is_too_large(error: ValueError) -> bool:
    message = str(error).lower()
    return "exceed" in message and "size" in message


def _file_size(path: Path) -> int:
    return path.lstat().st_size


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _remove_guarded_uuid_directory(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve(strict=True)
    absolute_candidate = Path(os.path.abspath(candidate))
    if absolute_candidate.parent != root_resolved:
        raise ValueError("Music job directory must be directly beneath root")
    UUID(absolute_candidate.name)

    try:
        candidate_stat = absolute_candidate.lstat()
    except FileNotFoundError:
        return
    file_attributes = getattr(candidate_stat, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(candidate_stat.st_mode) or bool(
        file_attributes & reparse_attribute
    ):
        raise ValueError("Refusing to clean a linked music job directory")
    if not stat.S_ISDIR(candidate_stat.st_mode):
        raise ValueError("Music job path is not a directory")
    shutil.rmtree(absolute_candidate)

import logging
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.models.music_artifact import MusicArtifactResponse, MusicMetadata
from app.services.artifact_store import ArtifactRecord
from app.services.music_artifact_service import (
    MusicArtifactTooLarge,
    MusicCapacityExceeded,
    MusicFetchTimeout,
    MusicProviderFailed,
)


TRACK_ID = "1234567890123456789012"
ARTIFACT_ID = "293c4b79-aa20-4d0b-a1b7-2d0a201a45b4"


class FakeMusicService:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[tuple[str, str | None]] = []

    async def create(self, track_id: str, request_id: str | None = None):
        self.requests.append((track_id, request_id))
        if self.error:
            raise self.error
        return self.response


class FakeStore:
    def __init__(self, record: ArtifactRecord | None) -> None:
        self.record = record
        self.deleted: list[str] = []

    async def get(self, artifact_id: str):
        return self.record

    async def delete(self, artifact_id: str):
        self.deleted.append(artifact_id)
        self.record = None
        return True


def response_model() -> MusicArtifactResponse:
    return MusicArtifactResponse(
        artifact_id=ARTIFACT_ID,
        track_id=TRACK_ID,
        filename="song.flac",
        size_bytes=4,
        sha256="hash",
        expires_at=datetime(2026, 8, 15, tzinfo=UTC),
        metadata=MusicMetadata(genre="Rock"),
    )


def services(music_service, store):
    return SimpleNamespace(
        ready=True,
        device="cpu",
        music_artifact_service=music_service,
        artifact_store=store,
    )


async def request(app, method: str, path: str, **kwargs):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_post_returns_exact_camel_case_contract(tmp_path: Path):
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(FakeMusicService(response_model()), FakeStore(None)),
    )

    response = await request(
        app,
        "POST",
        "/api/v1/music/artifacts",
        json={"trackId": TRACK_ID},
    )

    assert response.status_code == 201
    assert response.json() == {
        "artifactId": ARTIFACT_ID,
        "trackId": TRACK_ID,
        "filename": "song.flac",
        "contentType": "audio/flac",
        "sizeBytes": 4,
        "sha256": "hash",
        "expiresAt": "2026-08-15T00:00:00Z",
        "metadata": {
            "title": None,
            "artist": None,
            "album": None,
            "albumArtist": None,
            "composer": None,
            "genre": "Rock",
            "lyrics": None,
        },
    }


@pytest.mark.asyncio
async def test_post_passes_response_request_id_to_music_service(
    tmp_path: Path,
):
    music_service = FakeMusicService(response_model())
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(music_service, FakeStore(None)),
    )

    response = await request(
        app,
        "POST",
        "/api/v1/music/artifacts",
        json={"trackId": TRACK_ID},
    )

    assert music_service.requests == [
        (TRACK_ID, response.headers["x-request-id"])
    ]


@pytest.mark.asyncio
async def test_invalid_track_id_returns_safe_400(tmp_path: Path):
    app = create_app(Settings(artifact_root=tmp_path), services(FakeMusicService(), FakeStore(None)))

    response = await request(
        app, "POST", "/api/v1/music/artifacts", json={"trackId": "invalid"}
    )

    assert_problem(response, 400, "MUSIC_TRACK_ID_INVALID")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (MusicCapacityExceeded(), 429, "MUSIC_FETCH_CAPACITY_EXCEEDED"),
        (
            MusicArtifactTooLarge(
                "Downloaded artifact exceeds configured size limit"
            ),
            413,
            "MUSIC_ARTIFACT_TOO_LARGE",
        ),
        (
            MusicProviderFailed(
                "raw CLI output access token acquired: secret"
            ),
            502,
            "MUSIC_PROVIDER_FAILED",
        ),
        (MusicFetchTimeout("raw process timeout"), 504, "MUSIC_FETCH_TIMEOUT"),
    ],
)
async def test_post_maps_expected_failures_to_safe_problems(
    tmp_path: Path, error: Exception, status: int, code: str
):
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(FakeMusicService(error=error), FakeStore(None)),
    )

    response = await request(
        app, "POST", "/api/v1/music/artifacts", json={"trackId": TRACK_ID}
    )

    assert_problem(response, status, code)
    assert "raw" not in response.text
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_provider_failure_logs_sanitized_detail_and_request_id(
    tmp_path: Path,
    caplog,
):
    error = MusicProviderFailed(
        "provider failed: Authorization: Bearer provider-secret"
    )
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(FakeMusicService(error=error), FakeStore(None)),
    )

    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = await request(
            app,
            "POST",
            "/api/v1/music/artifacts",
            json={"trackId": TRACK_ID},
        )

    assert_problem(response, 502, "MUSIC_PROVIDER_FAILED")
    assert "music_provider_failed" in caplog.text
    assert response.headers["x-request-id"] in caplog.text
    assert "authorization=[redacted]" in caplog.text.lower()
    assert "provider-secret" not in caplog.text


@pytest.mark.asyncio
async def test_unexpected_error_returns_safe_500(tmp_path: Path):
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(FakeMusicService(error=KeyError("private detail")), FakeStore(None)),
    )

    response = await request(
        app, "POST", "/api/v1/music/artifacts", json={"trackId": TRACK_ID}
    )

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert "private detail" not in response.text


@pytest.mark.asyncio
async def test_get_streams_flac_with_content_length(tmp_path: Path):
    job = tmp_path / "job"
    job.mkdir()
    audio = job / "song.flac"
    audio.write_bytes(b"flac")
    model = response_model()
    record = ArtifactRecord(**model.model_dump(), file_path=audio)
    app = create_app(
        Settings(artifact_root=tmp_path),
        services(FakeMusicService(), FakeStore(record)),
    )

    response = await request(
        app, "GET", f"/api/v1/music/artifacts/{ARTIFACT_ID}/audio"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/flac"
    assert response.headers["content-length"] == "4"
    assert response.content == b"flac"
    assert "application/json" not in response.headers["content-type"]


@pytest.mark.asyncio
async def test_expired_get_returns_safe_404(tmp_path: Path):
    app = create_app(Settings(artifact_root=tmp_path), services(FakeMusicService(), FakeStore(None)))

    response = await request(
        app, "GET", f"/api/v1/music/artifacts/{ARTIFACT_ID}/audio"
    )

    assert_problem(response, 404, "MUSIC_ARTIFACT_NOT_FOUND")


@pytest.mark.asyncio
async def test_delete_is_idempotent(tmp_path: Path):
    store = FakeStore(None)
    app = create_app(Settings(artifact_root=tmp_path), services(FakeMusicService(), store))

    first = await request(app, "DELETE", f"/api/v1/music/artifacts/{ARTIFACT_ID}")
    second = await request(app, "DELETE", f"/api/v1/music/artifacts/{ARTIFACT_ID}")

    assert first.status_code == 204
    assert second.status_code == 204
    assert store.deleted == [ARTIFACT_ID, ARTIFACT_ID]


def assert_problem(response, status: int, code: str) -> None:
    assert response.status_code == status
    payload = response.json()
    assert payload["error"]["code"] == code
    request_id = payload["error"]["requestId"]
    assert UUID(request_id)
    assert response.headers["x-request-id"] == request_id

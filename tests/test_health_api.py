import httpx
import pytest
import torch

from app.config import Settings
import app.main as app_main
from app.main import create_app, create_uninitialized_services


class FakeReadiness:
    def __init__(self, ready: bool, device: str = "cpu"):
        self.ready = ready
        self.device = device


@pytest.mark.asyncio
async def test_liveness_is_available_before_readiness():
    app = create_app(Settings(), FakeReadiness(ready=False))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


@pytest.mark.asyncio
async def test_readiness_returns_service_unavailable_when_not_ready():
    app = create_app(Settings(), FakeReadiness(ready=False))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


@pytest.mark.asyncio
async def test_readiness_includes_selected_device_when_ready():
    app = create_app(Settings(), FakeReadiness(ready=True, device="cpu"))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "device": "cpu"}


@pytest.mark.asyncio
async def test_readiness_serializes_torch_device():
    app = create_app(Settings(), FakeReadiness(ready=True, device=torch.device("cpu")))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["device"] == "cpu"


@pytest.mark.asyncio
async def test_default_runtime_services_become_ready_during_lifespan(
    monkeypatch, tmp_path
):
    instances = {}

    class FakeNsfwChecker:
        @classmethod
        def load(cls, settings, device):
            instances["nsfw"] = (settings, device)
            return cls()

    class FakeStore:
        def __init__(self, *args, **kwargs):
            instances["store"] = self

        async def remove_stale_directories(self):
            instances["stale_removed"] = True

        async def sweep_expired(self):
            return 0

    class FakeComponent:
        def __init__(self, *args, **kwargs):
            self.args = args

    monkeypatch.setattr(app_main, "select_device", lambda value: "cpu")
    monkeypatch.setattr(app_main, "NsfwChecker", FakeNsfwChecker)
    monkeypatch.setattr(app_main, "ArtifactStore", FakeStore, raising=False)
    monkeypatch.setattr(app_main, "ProcessRunner", FakeComponent, raising=False)
    monkeypatch.setattr(app_main, "SpotiFlacDownloader", FakeComponent, raising=False)
    monkeypatch.setattr(app_main, "FfprobeMetadataReader", FakeComponent, raising=False)
    monkeypatch.setattr(app_main, "MusicArtifactService", FakeComponent, raising=False)

    settings = Settings(
        artifact_root=tmp_path / "music",
        image_scan_temp_root=tmp_path / "images",
    )
    services = create_uninitialized_services()
    app = create_app(settings, services)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready", "device": "cpu"}
        assert services.nsfw_checker is not None
        assert services.artifact_store is instances["store"]
        assert services.music_artifact_service is not None
        assert instances["stale_removed"] is True

    assert services.ready is False

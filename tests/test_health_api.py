import httpx
import pytest
import torch

from app.config import Settings
from app.main import create_app


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

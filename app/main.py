from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app(settings: Settings, services: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.services = services
        async with _lifespan(app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(health_router)
    return app


def create_uninitialized_services() -> SimpleNamespace:
    return SimpleNamespace(ready=False, device="uninitialized")

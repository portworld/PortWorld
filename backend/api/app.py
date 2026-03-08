from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes.health import router as health_router
from backend.api.routes.session_ws import router as session_ws_router
from backend.api.routes.vision import router as vision_router
from backend.core.settings import Settings
from backend.core.runtime import AppRuntime


def _make_lifespan(settings: Settings) -> Callable[[FastAPI], AsyncIterator[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = AppRuntime.from_settings(settings)
        yield

    return lifespan


def create_app() -> FastAPI:
    settings = Settings.from_env()
    app = FastAPI(title="loopa-mock-backend", lifespan=_make_lifespan(settings))

    allow_all = settings.cors_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(vision_router)
    app.include_router(session_ws_router)
    return app


app = create_app()

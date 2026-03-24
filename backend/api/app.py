from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.routes.health import router as health_router
from backend.api.routes.memory_admin import router as memory_admin_router
from backend.api.routes.user_memory import router as user_memory_router
from backend.api.routes.session_ws import router as session_ws_router
from backend.api.routes.vision import router as vision_router
from backend.core.constants import SERVICE_NAME
from backend.core.settings import Settings, load_environment_files
from backend.core.runtime import AppRuntime

logger = logging.getLogger(__name__)


class _PayloadTooLarge(Exception):
    pass


class VisionPayloadLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_request_bytes: int) -> None:
        self._app = app
        self._max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"].upper() == "POST"
            and scope["path"] == "/vision/frame"
            and self._max_request_bytes > 0
        ):
            total_bytes = 0
            response_started = False

            async def counting_receive() -> Message:
                nonlocal total_bytes
                message = await receive()
                if message["type"] != "http.request":
                    return message

                body = message.get("body", b"")
                total_bytes += len(body)
                if total_bytes > self._max_request_bytes:
                    raise _PayloadTooLarge
                return message

            async def tracking_send(message: Message) -> None:
                nonlocal response_started
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)

            try:
                await self._app(scope, counting_receive, tracking_send)
            except _PayloadTooLarge:
                logger.warning(
                    "Rejected oversized vision request path=%s bytes=%s limit=%s",
                    scope["path"],
                    total_bytes,
                    self._max_request_bytes,
                )
                if not response_started:
                    await _vision_payload_too_large_response(
                        scope=scope,
                        send=send,
                        max_request_bytes=self._max_request_bytes,
                    )
                return
            return

        await self._app(scope, receive, send)


class HealthAwareTrustedHostMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_hosts: list[str]) -> None:
        self._app = app
        self._trusted = TrustedHostMiddleware(app, allowed_hosts=allowed_hosts)
        self._health_paths = {"/livez", "/readyz"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # ALB health checks may use an IP-based Host header; bypass host checks
        # only for explicit health endpoints and keep validation for all other paths.
        if scope["type"] == "http" and scope["path"] in self._health_paths:
            await self._app(scope, receive, send)
            return
        await self._trusted(scope, receive, send)


async def _vision_payload_too_large_response(
    *,
    scope: Scope,
    send: Send,
    max_request_bytes: int,
) -> None:
    response = JSONResponse(
        status_code=413,
        content={
            "detail": (
                "Vision request exceeds "
                f"BACKEND_MAX_VISION_REQUEST_BYTES={max_request_bytes}"
            )
        },
    )
    await response(scope, _unused_receive, send)


async def _unused_receive() -> Message:
    return {
        "type": "http.request",
        "body": b"",
        "more_body": False,
    }


def _make_lifespan(settings: Settings) -> Callable[[FastAPI], AsyncIterator[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = AppRuntime.from_settings(settings)
        await runtime.startup()
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.shutdown()

    return lifespan


def create_app_from_settings(settings: Settings) -> FastAPI:
    settings.validate_production_posture()
    app = FastAPI(title=SERVICE_NAME, lifespan=_make_lifespan(settings))

    allow_all = settings.cors_origins == ["*"]
    app.add_middleware(
        HealthAwareTrustedHostMiddleware,
        allowed_hosts=settings.backend_allowed_hosts,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        VisionPayloadLimitMiddleware,
        max_request_bytes=settings.backend_max_vision_request_bytes,
    )

    app.include_router(health_router)
    app.include_router(memory_admin_router)
    app.include_router(user_memory_router)
    app.include_router(vision_router)
    app.include_router(session_ws_router)
    return app


def create_app() -> FastAPI:
    load_environment_files()
    return create_app_from_settings(Settings.from_env())


app = create_app()

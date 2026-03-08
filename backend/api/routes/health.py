from __future__ import annotations

from fastapi import APIRouter
from fastapi import Request

from backend.core.runtime import get_app_runtime

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    runtime = get_app_runtime(request.app)
    return {
        "status": "ok",
        "service": "loopa-mock-backend",
        "model": runtime.settings.openai_realtime_model,
        "ws_path": "/ws/session",
        "mock_capture_mode": (
            "enabled"
            if runtime.settings.backend_debug_mock_capture_mode
            else "disabled"
        ),
    }

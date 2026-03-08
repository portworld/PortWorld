from __future__ import annotations

from fastapi import APIRouter
from fastapi import Request

from backend.core.constants import SERVICE_NAME
from backend.core.runtime import get_app_runtime

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    runtime = get_app_runtime(request.app)
    storage_state = "ready" if runtime.storage_bootstrap_result is not None else "uninitialized"
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "realtime_provider": runtime.realtime_provider.provider_name,
        "realtime_model": runtime.settings.openai_realtime_model,
        "storage": storage_state,
        "ws_path": "/ws/session",
        "vision_path": "/vision/frame",
        "mock_capture_mode": (
            "enabled"
            if runtime.settings.backend_debug_mock_capture_mode
            else "disabled"
        ),
    }

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import JSONResponse

from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import SearchProviderFactory
from backend.vision.factory import VisionAnalyzerFactory
from backend.core.constants import SERVICE_NAME
from backend.core.runtime import get_app_runtime

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
    }


def _readiness_checks(request: Request) -> list[dict[str, Any]]:
    runtime = get_app_runtime(request.app)
    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "name": "storage_bootstrap",
            "ok": runtime.storage_bootstrap_result is not None,
            "detail": "storage is bootstrapped at startup",
        }
    )
    try:
        RealtimeProviderFactory(settings=runtime.settings).validate_configuration()
        checks.append({"name": "realtime_provider_configuration", "ok": True})
    except RuntimeError as exc:
        checks.append(
            {
                "name": "realtime_provider_configuration",
                "ok": False,
                "detail": str(exc),
            }
        )
    try:
        if runtime.settings.vision_memory_enabled:
            VisionAnalyzerFactory(settings=runtime.settings).validate_configuration()
        checks.append({"name": "vision_provider_configuration", "ok": True})
    except RuntimeError as exc:
        checks.append(
            {
                "name": "vision_provider_configuration",
                "ok": False,
                "detail": str(exc),
            }
        )
    try:
        if runtime.settings.realtime_tooling_enabled:
            SearchProviderFactory(settings=runtime.settings)
        checks.append({"name": "realtime_tooling_configuration", "ok": True})
    except RuntimeError as exc:
        checks.append(
            {
                "name": "realtime_tooling_configuration",
                "ok": False,
                "detail": str(exc),
            }
        )
    checks.append(
        {
            "name": "production_profile_posture",
            "ok": (
                not runtime.settings.is_production_profile
                or bool(runtime.settings.backend_bearer_token)
            ),
        }
    )
    return checks


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks = _readiness_checks(request)
    ready = all(bool(check.get("ok")) for check in checks)
    status = "ready" if ready else "not_ready"
    payload = {
        "status": status,
        "service": SERVICE_NAME,
        "checks": checks,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)

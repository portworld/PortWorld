from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.auth import require_http_bearer_auth
from backend.realtime.factory import RealtimeProviderFactory
from backend.tools.runtime import SearchProviderFactory
from backend.vision.factory import VisionAnalyzerFactory
from backend.core.constants import SERVICE_NAME
from backend.core.runtime import get_app_runtime

router = APIRouter()


class HealthStatusResponse(BaseModel):
    status: str
    service: str


@router.get("/healthz", response_model=HealthStatusResponse)
async def healthz(request: Request) -> HealthStatusResponse:
    return HealthStatusResponse(status="ok", service=SERVICE_NAME)


def _readiness_checks(request: Request, *, redact_details: bool) -> list[dict[str, Any]]:
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
        detail = "check_failed" if redact_details else str(exc)
        checks.append(
            {
                "name": "realtime_provider_configuration",
                "ok": False,
                "detail": detail,
            }
        )
    try:
        if runtime.settings.vision_memory_enabled:
            VisionAnalyzerFactory(settings=runtime.settings).validate_configuration()
        checks.append({"name": "vision_provider_configuration", "ok": True})
    except RuntimeError as exc:
        detail = "check_failed" if redact_details else str(exc)
        checks.append(
            {
                "name": "vision_provider_configuration",
                "ok": False,
                "detail": detail,
            }
        )
    try:
        if runtime.settings.realtime_tooling_enabled:
            SearchProviderFactory(settings=runtime.settings)
        checks.append({"name": "realtime_tooling_configuration", "ok": True})
    except RuntimeError as exc:
        detail = "check_failed" if redact_details else str(exc)
        checks.append(
            {
                "name": "realtime_tooling_configuration",
                "ok": False,
                "detail": detail,
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
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    checks = _readiness_checks(
        request,
        redact_details=runtime.settings.is_production_profile,
    )
    ready = all(bool(check.get("ok")) for check in checks)
    status = "ready" if ready else "not_ready"
    payload = {
        "status": status,
        "service": SERVICE_NAME,
        "checks": checks,
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)

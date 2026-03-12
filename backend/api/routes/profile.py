from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.core.auth import require_http_bearer_auth
from backend.core.runtime import get_app_runtime
from backend.memory.lifecycle import PROFILE_METADATA_KEY, allowed_profile_fields

router = APIRouter()


class ProfileResponse(BaseModel):
    profile: dict[str, Any]
    is_onboarded: bool
    missing_fields: list[str]
    metadata: dict[str, Any]


class ProfileUpdatePayload(BaseModel):
    name: str | None = None
    job: str | None = None
    company: str | None = None
    preferences: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "job", "company")
    @classmethod
    def validate_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("preferences", "projects")
    @classmethod
    def validate_string_lists(
        cls,
        value: list[str],
    ) -> list[str]:
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if candidate:
                normalized.append(candidate)
        return normalized


def _build_profile_response(profile_payload: dict[str, Any]) -> ProfileResponse:
    fields = allowed_profile_fields()
    profile = {
        field_name: profile_payload[field_name]
        for field_name in fields
        if field_name in profile_payload
    }
    metadata = profile_payload.get(PROFILE_METADATA_KEY)
    if not isinstance(metadata, dict):
        metadata = {}
    present_fields = set(profile.keys())
    return ProfileResponse(
        profile=profile,
        is_onboarded=bool(profile),
        missing_fields=[
            field_name for field_name in fields if field_name not in present_fields
        ],
        metadata=metadata,
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(request: Request) -> ProfileResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    profile = await asyncio.to_thread(runtime.storage.read_user_profile)
    return _build_profile_response(profile)


@router.put("/profile", response_model=ProfileResponse)
async def put_profile(
    request: Request,
    payload: ProfileUpdatePayload,
) -> ProfileResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    updated_profile = await asyncio.to_thread(
        runtime.storage.write_user_profile,
        payload=payload.model_dump(),
        source="api_profile_put",
    )
    return _build_profile_response(updated_profile)


@router.post("/profile/reset", response_model=ProfileResponse)
async def reset_profile(request: Request) -> ProfileResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    profile = await asyncio.to_thread(runtime.storage.reset_user_profile)
    return _build_profile_response(profile)

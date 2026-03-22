from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.core.auth import require_http_bearer_auth
from backend.core.rate_limit import enforce_http_rate_limit
from backend.core.runtime import get_app_runtime
from backend.memory.lifecycle import PROFILE_METADATA_KEY, allowed_profile_fields

router = APIRouter()


class UserMemoryResponse(BaseModel):
    user_memory: dict[str, Any]
    is_onboarded: bool
    missing_fields: list[str]
    metadata: dict[str, Any]


class ProfileUpdatePayload(BaseModel):
    name: str | None = None
    job: str | None = None
    company: str | None = None
    preferred_language: str | None = None
    location: str | None = None
    intended_use: str | None = None
    preferences: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "name",
        "job",
        "company",
        "preferred_language",
        "location",
        "intended_use",
    )
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


def _build_user_memory_response(profile_payload: dict[str, Any]) -> UserMemoryResponse:
    fields = allowed_profile_fields()
    user_memory = {
        field_name: profile_payload[field_name]
        for field_name in fields
        if field_name in profile_payload
    }
    metadata = profile_payload.get(PROFILE_METADATA_KEY)
    if not isinstance(metadata, dict):
        metadata = {}
    present_fields = set(user_memory.keys())
    return UserMemoryResponse(
        user_memory=user_memory,
        is_onboarded=bool(user_memory),
        missing_fields=[
            field_name for field_name in fields if field_name not in present_fields
        ],
        metadata=metadata,
    )


@router.get("/memory/user", response_model=UserMemoryResponse)
async def get_user_memory(request: Request) -> UserMemoryResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_user_get")
    profile = await asyncio.to_thread(runtime.storage.read_user_profile)
    return _build_user_memory_response(profile)


@router.put("/memory/user", response_model=UserMemoryResponse)
async def put_user_memory(
    request: Request,
    payload: ProfileUpdatePayload,
) -> UserMemoryResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_user_put")
    updated_profile = await asyncio.to_thread(
        runtime.storage.write_user_profile,
        payload=payload.model_dump(),
        source="api_user_memory_put",
    )
    return _build_user_memory_response(updated_profile)


@router.post("/memory/user/reset", response_model=UserMemoryResponse)
async def reset_user_memory(request: Request) -> UserMemoryResponse:
    runtime = get_app_runtime(request.app)
    require_http_bearer_auth(request=request, settings=runtime.settings)
    await enforce_http_rate_limit(request, "memory_user_reset")
    profile = await asyncio.to_thread(runtime.storage.reset_user_profile)
    return _build_user_memory_response(profile)

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


SUPPORTED_OBJECT_STORE_PROVIDERS = {"filesystem", "gcs", "s3", "azure_blob"}


@dataclass(frozen=True, slots=True)
class BackendEnvContract:
    backend_profile: str
    backend_bearer_token: str | None
    backend_object_store_provider: str
    backend_object_store_name: str | None
    backend_object_store_endpoint: str | None
    backend_object_store_prefix: str | None

    @property
    def is_production_profile(self) -> bool:
        return self.backend_profile in {"prod", "production"}

    @property
    def backend_storage_backend(self) -> str:
        return "local" if self.backend_object_store_provider == "filesystem" else "managed"


def build_backend_env_contract(values: Mapping[str, object]) -> BackendEnvContract:
    return BackendEnvContract(
        backend_profile=_normalize_text(values.get("BACKEND_PROFILE")) or "development",
        backend_bearer_token=_normalize_text(values.get("BACKEND_BEARER_TOKEN")),
        backend_object_store_provider=(
            _normalize_text(values.get("BACKEND_OBJECT_STORE_PROVIDER")) or "filesystem"
        ),
        backend_object_store_name=_normalize_text(values.get("BACKEND_OBJECT_STORE_NAME")),
        backend_object_store_endpoint=_normalize_text(values.get("BACKEND_OBJECT_STORE_ENDPOINT")),
        backend_object_store_prefix=_normalize_text(values.get("BACKEND_OBJECT_STORE_PREFIX")),
    )


def validate_production_posture(contract: BackendEnvContract) -> None:
    if not contract.is_production_profile:
        return
    if not contract.backend_bearer_token:
        raise RuntimeError(
            "BACKEND_BEARER_TOKEN must be set when BACKEND_PROFILE=production."
        )


def validate_storage_contract(contract: BackendEnvContract) -> None:
    if contract.backend_object_store_provider not in SUPPORTED_OBJECT_STORE_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_OBJECT_STORE_PROVIDERS))
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_PROVIDER must be one of "
            f"{supported}. Got {contract.backend_object_store_provider!r}."
        )

    if contract.backend_object_store_provider == "filesystem":
        if contract.backend_object_store_name is not None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_NAME must be unset when "
                "BACKEND_OBJECT_STORE_PROVIDER=filesystem."
            )
        if contract.backend_object_store_endpoint is not None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_ENDPOINT must be unset when "
                "BACKEND_OBJECT_STORE_PROVIDER=filesystem."
            )
        if contract.backend_object_store_prefix is not None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PREFIX must be unset when "
                "BACKEND_OBJECT_STORE_PROVIDER=filesystem."
            )
        return

    if contract.backend_object_store_name is None:
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_NAME must be set when "
            "BACKEND_OBJECT_STORE_PROVIDER is a cloud object store."
        )
    if (
        contract.backend_object_store_provider == "azure_blob"
        and contract.backend_object_store_endpoint is None
    ):
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_ENDPOINT must be set when "
            "BACKEND_OBJECT_STORE_PROVIDER=azure_blob."
        )
    if contract.backend_object_store_prefix is None:
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_PREFIX must be set when "
            "BACKEND_OBJECT_STORE_PROVIDER is a cloud object store."
        )


def validate_backend_env_contract(values: Mapping[str, object]) -> BackendEnvContract:
    contract = build_backend_env_contract(values)
    validate_production_posture(contract)
    validate_storage_contract(contract)
    return contract


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


STORAGE_BACKEND_MANAGED = "managed"
SUPPORTED_STORAGE_BACKENDS = {"local", STORAGE_BACKEND_MANAGED}
SUPPORTED_OBJECT_STORE_PROVIDERS = {"filesystem", "gcs", "s3", "azure_blob"}


@dataclass(frozen=True, slots=True)
class BackendEnvContract:
    backend_profile: str
    backend_bearer_token: str | None
    backend_storage_backend: str
    backend_database_url: str | None
    backend_object_store_provider: str
    backend_object_store_name: str | None
    backend_object_store_endpoint: str | None
    backend_object_store_prefix: str | None

    @property
    def is_production_profile(self) -> bool:
        return self.backend_profile in {"prod", "production"}


def build_backend_env_contract(values: Mapping[str, object]) -> BackendEnvContract:
    return BackendEnvContract(
        backend_profile=_normalize_text(values.get("BACKEND_PROFILE")) or "development",
        backend_bearer_token=_normalize_text(values.get("BACKEND_BEARER_TOKEN")),
        backend_storage_backend=_normalize_text(values.get("BACKEND_STORAGE_BACKEND")) or "local",
        backend_database_url=_normalize_text(values.get("BACKEND_DATABASE_URL")),
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
    if contract.backend_storage_backend not in SUPPORTED_STORAGE_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_STORAGE_BACKENDS))
        raise RuntimeError(
            "BACKEND_STORAGE_BACKEND must be one of "
            f"{supported}. Got {contract.backend_storage_backend!r}."
        )

    if contract.backend_object_store_provider not in SUPPORTED_OBJECT_STORE_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_OBJECT_STORE_PROVIDERS))
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_PROVIDER must be one of "
            f"{supported}. Got {contract.backend_object_store_provider!r}."
        )

    if contract.backend_storage_backend == "local":
        if contract.backend_object_store_provider != "filesystem":
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_PROVIDER must be 'filesystem' when "
                "BACKEND_STORAGE_BACKEND=local."
            )
        return

    if contract.backend_database_url is None:
        raise RuntimeError(
            "BACKEND_DATABASE_URL must be set when BACKEND_STORAGE_BACKEND=managed."
        )
    if contract.backend_object_store_provider == "filesystem":
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_PROVIDER cannot be 'filesystem' when "
            "BACKEND_STORAGE_BACKEND=managed."
        )
    if (
        contract.backend_object_store_provider == "azure_blob"
        and contract.backend_object_store_endpoint is None
    ):
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_ENDPOINT must be set when "
            "BACKEND_OBJECT_STORE_PROVIDER=azure_blob."
        )
    if contract.backend_object_store_name is None:
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_NAME must be set when BACKEND_STORAGE_BACKEND=managed."
        )
    if contract.backend_object_store_prefix is None:
        raise RuntimeError(
            "BACKEND_OBJECT_STORE_PREFIX must be set when BACKEND_STORAGE_BACKEND=managed."
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

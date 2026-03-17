from __future__ import annotations

from abc import ABC, abstractmethod


def normalize_object_store_relative_path(raw_path: str) -> str:
    candidate = raw_path.strip().replace("\\", "/")
    if not candidate or candidate.startswith("/"):
        raise ValueError("Object-store artifact path must be a relative non-empty path.")
    if "\x00" in candidate:
        raise ValueError("Object-store artifact path cannot contain null bytes.")
    if len(candidate) >= 2 and candidate[1] == ":" and candidate[0].isalpha():
        raise ValueError("Object-store artifact path cannot be drive-prefixed.")
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            "Object-store artifact path cannot contain empty, current-directory, "
            "or parent-directory segments."
        )
    return "/".join(parts)


def normalize_object_store_prefix(raw_prefix: str) -> str:
    prefix = raw_prefix.strip().replace("\\", "/").strip("/")
    if not prefix:
        return ""
    return normalize_object_store_relative_path(prefix)


class ObjectStore(ABC):
    def __init__(
        self,
        *,
        provider_name: str,
        store_name: str,
        key_prefix: str,
        endpoint: str | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.store_name = store_name
        # Compatibility alias while call sites migrate away from bucket naming.
        self.bucket_name = store_name
        self.key_prefix = normalize_object_store_prefix(key_prefix)
        self.endpoint = endpoint

    def resolve_location(self, *, relative_path: str) -> str:
        normalized = normalize_object_store_relative_path(relative_path)
        if not self.key_prefix:
            return normalized
        return f"{self.key_prefix}/{normalized}"

    def put_text(
        self,
        *,
        relative_path: str,
        content: str,
        content_type: str,
    ) -> None:
        self.put_bytes(
            relative_path=relative_path,
            content=content.encode("utf-8"),
            content_type=content_type,
        )

    def get_text(self, *, relative_path: str) -> str | None:
        payload = self.get_bytes(relative_path=relative_path)
        if payload is None:
            return None
        return payload.decode("utf-8")

    @abstractmethod
    def put_bytes(
        self,
        *,
        relative_path: str,
        content: bytes,
        content_type: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_bytes(self, *, relative_path: str) -> bytes | None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, *, relative_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def exists(self, *, relative_path: str) -> bool:
        raise NotImplementedError


def build_object_store(
    *,
    provider: str,
    store_name: str | None = None,
    bucket_name: str | None = None,
    key_prefix: str,
    endpoint: str | None = None,
) -> ObjectStore:
    resolved_store_name = (store_name or bucket_name or "").strip()
    if not resolved_store_name:
        raise RuntimeError("Managed object store name is required.")

    if provider == "gcs":
        from backend.infrastructure.storage.gcs import GCSObjectStore

        return GCSObjectStore(
            store_name=resolved_store_name,
            endpoint=endpoint,
            key_prefix=key_prefix,
        )
    if provider == "s3":
        from backend.infrastructure.storage.s3 import S3ObjectStore

        return S3ObjectStore(
            store_name=resolved_store_name,
            endpoint=endpoint,
            key_prefix=key_prefix,
        )
    if provider == "azure_blob":
        from backend.infrastructure.storage.azure_blob import AzureBlobObjectStore

        return AzureBlobObjectStore(
            store_name=resolved_store_name,
            endpoint=endpoint,
            key_prefix=key_prefix,
        )
    raise RuntimeError(f"Unsupported managed object store provider: {provider!r}")

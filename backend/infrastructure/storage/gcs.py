from __future__ import annotations

from backend.infrastructure.storage.object_store import ObjectStore

try:
    from google.api_core.exceptions import NotFound
    from google.cloud import storage
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime.
    class NotFound(Exception):
        """Fallback exception type so optional-import handlers remain valid."""

    storage = None


class GCSObjectStore(ObjectStore):
    def __init__(
        self,
        *,
        store_name: str,
        endpoint: str | None,
        key_prefix: str,
    ) -> None:
        if storage is None or NotFound is None:
            raise RuntimeError(
                "Managed GCS storage requires google-cloud-storage. Install the backend "
                "dependencies before using BACKEND_OBJECT_STORE_PROVIDER=gcs."
            )
        super().__init__(
            provider_name="gcs",
            store_name=store_name,
            key_prefix=key_prefix,
            endpoint=endpoint,
        )
        self._client = storage.Client(client_options=None if endpoint is None else {"api_endpoint": endpoint})
        self._bucket = self._client.bucket(store_name)

    def put_bytes(
        self,
        *,
        relative_path: str,
        content: bytes,
        content_type: str,
    ) -> None:
        blob = self._bucket.blob(self.resolve_location(relative_path=relative_path))
        blob.upload_from_string(content, content_type=content_type)

    def get_bytes(self, *, relative_path: str) -> bytes | None:
        blob = self._bucket.blob(self.resolve_location(relative_path=relative_path))
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None

    def delete(self, *, relative_path: str) -> None:
        blob = self._bucket.blob(self.resolve_location(relative_path=relative_path))
        try:
            blob.delete()
        except NotFound:
            return

    def exists(self, *, relative_path: str) -> bool:
        blob = self._bucket.blob(self.resolve_location(relative_path=relative_path))
        return bool(blob.exists(client=self._client))

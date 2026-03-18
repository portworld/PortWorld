from __future__ import annotations

from backend.infrastructure.storage.object_store import ObjectStore

try:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime.
    ResourceNotFoundError = None
    DefaultAzureCredential = None
    BlobServiceClient = None


class AzureBlobObjectStore(ObjectStore):
    def __init__(
        self,
        *,
        store_name: str,
        endpoint: str | None,
        key_prefix: str,
    ) -> None:
        if DefaultAzureCredential is None or BlobServiceClient is None:
            raise RuntimeError(
                "Managed Azure Blob storage requires azure-storage-blob and azure-identity. "
                "Install backend dependencies before using BACKEND_OBJECT_STORE_PROVIDER=azure_blob."
            )
        if endpoint is None:
            raise RuntimeError(
                "BACKEND_OBJECT_STORE_ENDPOINT is required for BACKEND_OBJECT_STORE_PROVIDER=azure_blob."
            )
        super().__init__(
            provider_name="azure_blob",
            store_name=store_name,
            key_prefix=key_prefix,
            endpoint=endpoint,
        )
        credential = DefaultAzureCredential()
        self._service_client = BlobServiceClient(account_url=endpoint, credential=credential)
        self._container_client = self._service_client.get_container_client(store_name)

    def put_bytes(
        self,
        *,
        relative_path: str,
        content: bytes,
        content_type: str,
    ) -> None:
        blob_client = self._container_client.get_blob_client(self.resolve_location(relative_path=relative_path))
        blob_client.upload_blob(content, blob_type="BlockBlob", overwrite=True, content_type=content_type)

    def get_bytes(self, *, relative_path: str) -> bytes | None:
        blob_client = self._container_client.get_blob_client(self.resolve_location(relative_path=relative_path))
        if not blob_client.exists():
            return None
        downloader = blob_client.download_blob()
        return downloader.readall()

    def delete(self, *, relative_path: str) -> None:
        blob_client = self._container_client.get_blob_client(self.resolve_location(relative_path=relative_path))
        if ResourceNotFoundError is None:  # pragma: no cover - dependency-specific branch
            blob_client.delete_blob(delete_snapshots="include")
            return
        try:
            blob_client.delete_blob(delete_snapshots="include")
        except ResourceNotFoundError:
            return

    def exists(self, *, relative_path: str) -> bool:
        blob_client = self._container_client.get_blob_client(self.resolve_location(relative_path=relative_path))
        return bool(blob_client.exists())

from __future__ import annotations

from backend.infrastructure.storage.object_store import ObjectStore

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime.
    boto3 = None
    ClientError = Exception


class S3ObjectStore(ObjectStore):
    def __init__(
        self,
        *,
        store_name: str,
        endpoint: str | None,
        key_prefix: str,
    ) -> None:
        if boto3 is None:
            raise RuntimeError(
                "Managed S3 storage requires boto3. Install backend dependencies before "
                "using BACKEND_OBJECT_STORE_PROVIDER=s3."
            )
        super().__init__(
            provider_name="s3",
            store_name=store_name,
            key_prefix=key_prefix,
            endpoint=endpoint,
        )
        session = boto3.session.Session()
        self._client = session.client("s3", endpoint_url=endpoint or None)

    def put_bytes(
        self,
        *,
        relative_path: str,
        content: bytes,
        content_type: str,
    ) -> None:
        self._client.put_object(
            Bucket=self.store_name,
            Key=self.resolve_location(relative_path=relative_path),
            Body=content,
            ContentType=content_type,
        )

    def get_bytes(self, *, relative_path: str) -> bytes | None:
        try:
            result = self._client.get_object(
                Bucket=self.store_name,
                Key=self.resolve_location(relative_path=relative_path),
            )
            return result["Body"].read()
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404"}:
                return None
            raise

    def delete(self, *, relative_path: str) -> None:
        self._client.delete_object(
            Bucket=self.store_name,
            Key=self.resolve_location(relative_path=relative_path),
        )

    def exists(self, *, relative_path: str) -> bool:
        try:
            self._client.head_object(
                Bucket=self.store_name,
                Key=self.resolve_location(relative_path=relative_path),
            )
            return True
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.core.settings import Settings


class BackendStorageSettingsTests(unittest.TestCase):
    def _settings(self, extra_env: dict[str, str]) -> Settings:
        with mock.patch.dict(os.environ, extra_env, clear=True):
            return Settings.from_env()

    def test_legacy_managed_alias_is_rejected(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "postgres_gcs",
                "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                "BACKEND_OBJECT_STORE_PROVIDER": "gcs",
                "BACKEND_OBJECT_STORE_NAME": "bucket",
                "BACKEND_OBJECT_STORE_PREFIX": "svc",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "must be one of local, managed"):
            settings.validate_storage_contract()

    def test_managed_rejects_missing_object_store_name(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "managed",
                "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                "BACKEND_OBJECT_STORE_PROVIDER": "gcs",
                "BACKEND_OBJECT_STORE_PREFIX": "svc",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "BACKEND_OBJECT_STORE_NAME must be set"):
            settings.validate_storage_contract()

    def test_managed_rejects_filesystem_provider(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "managed",
                "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                "BACKEND_OBJECT_STORE_PROVIDER": "filesystem",
                "BACKEND_OBJECT_STORE_NAME": "bucket",
                "BACKEND_OBJECT_STORE_PREFIX": "svc",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "cannot be 'filesystem'"):
            settings.validate_storage_contract()

    def test_managed_azure_blob_requires_endpoint(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "managed",
                "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                "BACKEND_OBJECT_STORE_PROVIDER": "azure_blob",
                "BACKEND_OBJECT_STORE_NAME": "container",
                "BACKEND_OBJECT_STORE_PREFIX": "svc",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "BACKEND_OBJECT_STORE_ENDPOINT must be set"):
            settings.validate_storage_contract()

    def test_managed_provider_matrix_validates(self) -> None:
        for provider, endpoint in (
            ("gcs", None),
            ("s3", None),
            ("azure_blob", "https://pwstorage123.blob.core.windows.net"),
        ):
            with self.subTest(provider=provider):
                env = {
                    "BACKEND_STORAGE_BACKEND": "managed",
                    "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                    "BACKEND_OBJECT_STORE_PROVIDER": provider,
                    "BACKEND_OBJECT_STORE_NAME": "store-name",
                    "BACKEND_OBJECT_STORE_PREFIX": "svc",
                }
                if endpoint is not None:
                    env["BACKEND_OBJECT_STORE_ENDPOINT"] = endpoint
                settings = self._settings(env)
                settings.validate_storage_contract()

    def test_managed_rejects_bucket_alias_without_canonical_name(self) -> None:
        for provider, endpoint in (
            ("gcs", None),
            ("s3", None),
            ("azure_blob", "https://pwstorage123.blob.core.windows.net"),
        ):
            with self.subTest(provider=provider):
                env = {
                    "BACKEND_STORAGE_BACKEND": "managed",
                    "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                    "BACKEND_OBJECT_STORE_PROVIDER": provider,
                    "BACKEND_OBJECT_STORE_BUCKET": "legacy-store-name",
                    "BACKEND_OBJECT_STORE_PREFIX": "svc",
                }
                if endpoint is not None:
                    env["BACKEND_OBJECT_STORE_ENDPOINT"] = endpoint
                settings = self._settings(env)
                with self.assertRaisesRegex(RuntimeError, "BACKEND_OBJECT_STORE_NAME must be set"):
                    settings.validate_storage_contract()

    def test_local_requires_filesystem_provider(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "local",
                "BACKEND_OBJECT_STORE_PROVIDER": "gcs",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "must be 'filesystem'"):
            settings.validate_storage_contract()


if __name__ == "__main__":
    unittest.main()

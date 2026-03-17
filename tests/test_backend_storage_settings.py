from __future__ import annotations

import os
import unittest
from unittest import mock

from backend.core.settings import Settings


class BackendStorageSettingsTests(unittest.TestCase):
    def _settings(self, extra_env: dict[str, str]) -> Settings:
        with mock.patch.dict(os.environ, extra_env, clear=True):
            return Settings.from_env()

    def test_legacy_managed_alias_and_bucket_alias_are_normalized(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "postgres_gcs",
                "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                "BACKEND_OBJECT_STORE_PROVIDER": "gcs",
                "BACKEND_OBJECT_STORE_BUCKET": "legacy-bucket",
                "BACKEND_OBJECT_STORE_PREFIX": "svc",
            }
        )
        self.assertEqual(settings.backend_storage_backend, "managed")
        self.assertEqual(settings.backend_object_store_name, "legacy-bucket")
        settings.validate_storage_contract()

    def test_canonical_name_wins_over_bucket_alias(self) -> None:
        settings = self._settings(
            {
                "BACKEND_STORAGE_BACKEND": "managed",
                "BACKEND_DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
                "BACKEND_OBJECT_STORE_PROVIDER": "gcs",
                "BACKEND_OBJECT_STORE_NAME": "canonical-bucket",
                "BACKEND_OBJECT_STORE_BUCKET": "legacy-bucket",
                "BACKEND_OBJECT_STORE_PREFIX": "svc",
            }
        )
        self.assertEqual(settings.backend_object_store_name, "canonical-bucket")
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

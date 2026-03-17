from __future__ import annotations

import sys
from types import ModuleType
import unittest
from unittest import mock

from backend.infrastructure.storage.object_store import build_object_store


class BackendObjectStoreFactoryTests(unittest.TestCase):
    def _fake_module(self, class_name: str) -> tuple[ModuleType, type]:
        module = ModuleType("fake")

        class FakeStore:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        setattr(module, class_name, FakeStore)
        return module, FakeStore

    def test_dispatches_gcs_provider(self) -> None:
        module, fake_class = self._fake_module("GCSObjectStore")
        with mock.patch.dict(sys.modules, {"backend.infrastructure.storage.gcs": module}):
            store = build_object_store(
                provider="gcs",
                store_name="bucket-a",
                endpoint="https://gcs.test",
                key_prefix="svc",
            )
        self.assertIsInstance(store, fake_class)
        self.assertEqual(
            store.kwargs,
            {
                "store_name": "bucket-a",
                "endpoint": "https://gcs.test",
                "key_prefix": "svc",
            },
        )

    def test_dispatches_s3_provider(self) -> None:
        module, fake_class = self._fake_module("S3ObjectStore")
        with mock.patch.dict(sys.modules, {"backend.infrastructure.storage.s3": module}):
            store = build_object_store(
                provider="s3",
                store_name="bucket-b",
                endpoint=None,
                key_prefix="svc",
            )
        self.assertIsInstance(store, fake_class)
        self.assertEqual(
            store.kwargs,
            {
                "store_name": "bucket-b",
                "endpoint": None,
                "key_prefix": "svc",
            },
        )

    def test_dispatches_azure_provider(self) -> None:
        module, fake_class = self._fake_module("AzureBlobObjectStore")
        with mock.patch.dict(sys.modules, {"backend.infrastructure.storage.azure_blob": module}):
            store = build_object_store(
                provider="azure_blob",
                store_name="container-a",
                endpoint="https://acct.blob.core.windows.net",
                key_prefix="svc",
            )
        self.assertIsInstance(store, fake_class)
        self.assertEqual(
            store.kwargs,
            {
                "store_name": "container-a",
                "endpoint": "https://acct.blob.core.windows.net",
                "key_prefix": "svc",
            },
        )

    def test_bucket_alias_is_accepted_when_store_name_is_missing(self) -> None:
        module, fake_class = self._fake_module("GCSObjectStore")
        with mock.patch.dict(sys.modules, {"backend.infrastructure.storage.gcs": module}):
            store = build_object_store(
                provider="gcs",
                bucket_name="legacy-bucket",
                endpoint=None,
                key_prefix="svc",
            )
        self.assertIsInstance(store, fake_class)
        self.assertEqual(store.kwargs["store_name"], "legacy-bucket")

    def test_empty_store_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Managed object store name is required"):
            build_object_store(
                provider="gcs",
                store_name="",
                endpoint=None,
                key_prefix="svc",
            )

    def test_unsupported_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsupported managed object store provider"):
            build_object_store(
                provider="filesystem",
                store_name="bucket-a",
                endpoint=None,
                key_prefix="svc",
            )


if __name__ == "__main__":
    unittest.main()

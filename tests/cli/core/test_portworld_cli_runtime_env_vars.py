from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace
import unittest

from portworld_cli.deploy.stages.runtime import build_runtime_env_vars


class RuntimeEnvVarsTests(unittest.TestCase):
    def test_managed_gcp_env_emits_canonical_keys(self) -> None:
        env_values = OrderedDict(
            [
                ("BACKEND_DATA_DIR", "backend/var"),
                ("PORT", "8080"),
                ("FOO", "bar"),
            ]
        )
        config = SimpleNamespace(service_name="portworld-api")

        env_vars = build_runtime_env_vars(
            env_values=env_values,
            config=config,
            bucket_name="gcp-managed-bucket",
        )

        self.assertEqual(env_vars["BACKEND_STORAGE_BACKEND"], "managed")
        self.assertEqual(env_vars["BACKEND_OBJECT_STORE_PROVIDER"], "gcs")
        self.assertEqual(env_vars["BACKEND_OBJECT_STORE_NAME"], "gcp-managed-bucket")
        self.assertEqual(env_vars["BACKEND_OBJECT_STORE_PREFIX"], "portworld-api")
        self.assertNotIn("BACKEND_DATA_DIR", env_vars)
        self.assertNotIn("PORT", env_vars)

    def test_managed_gcp_env_preserves_extension_env_values(self) -> None:
        env_values = OrderedDict(
            [
                ("PORTWORLD_EXTENSIONS_MANIFEST", "/app/.portworld/extensions.json"),
                ("PORTWORLD_EXTENSIONS_PYTHON_PATH", "/app/.portworld/extensions/python"),
                ("BACKEND_DATA_DIR", "backend/var"),
            ]
        )
        config = SimpleNamespace(service_name="portworld-api")

        env_vars = build_runtime_env_vars(
            env_values=env_values,
            config=config,
            bucket_name="gcp-managed-bucket",
        )

        self.assertEqual(
            env_vars["PORTWORLD_EXTENSIONS_MANIFEST"],
            "/app/.portworld/extensions.json",
        )
        self.assertEqual(
            env_vars["PORTWORLD_EXTENSIONS_PYTHON_PATH"],
            "/app/.portworld/extensions/python",
        )


if __name__ == "__main__":
    unittest.main()

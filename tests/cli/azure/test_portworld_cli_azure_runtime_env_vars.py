from __future__ import annotations

from collections import OrderedDict
import unittest

from portworld_cli.azure.deploy import _ResolvedAzureDeployConfig, _build_runtime_env_vars


class AzureRuntimeEnvVarsTests(unittest.TestCase):
    def test_managed_azure_blob_env_contract(self) -> None:
        env_values = OrderedDict(
            [
                ("BACKEND_DATA_DIR", "backend/var"),
                ("PORT", "8080"),
                ("FOO", "bar"),
            ]
        )
        config = _ResolvedAzureDeployConfig(
            runtime_source="source",
            image_source_mode="source_build",
            subscription_id="sub-123",
            tenant_id="tenant-456",
            resource_group="rg-portworld",
            region="westeurope",
            environment_name="pw-env",
            app_name="pw-api",
            database_url="postgresql://user:pass@db.example:5432/app",
            storage_account="pwstorage123",
            blob_container="pw-artifacts",
            blob_endpoint="https://pwstorage123.blob.core.windows.net",
            acr_name="pwpwapi123",
            acr_server="example.azurecr.io",
            acr_repo="pw-api-backend",
            postgres_server_name="pwpgpwapi123",
            postgres_database_name="portworld",
            postgres_admin_username="pwadmin",
            image_tag="abc123",
            image_uri="example.azurecr.io/pw-api-backend:abc123",
            cors_origins="https://app.example.com",
            allowed_hosts="api.example.com",
            published_release_tag=None,
            published_image_ref=None,
        )

        env = _build_runtime_env_vars(
            env_values,
            config,
            database_url="postgresql://user:pass@db.example:5432/app",
        )
        self.assertEqual(env["BACKEND_STORAGE_BACKEND"], "managed")
        self.assertEqual(env["BACKEND_OBJECT_STORE_PROVIDER"], "azure_blob")
        self.assertEqual(env["BACKEND_OBJECT_STORE_NAME"], "pw-artifacts")
        self.assertEqual(env["BACKEND_OBJECT_STORE_BUCKET"], "pw-artifacts")
        self.assertEqual(env["BACKEND_OBJECT_STORE_ENDPOINT"], "https://pwstorage123.blob.core.windows.net")
        self.assertEqual(env["BACKEND_OBJECT_STORE_PREFIX"], "pw-api")
        self.assertEqual(env["BACKEND_DATABASE_URL"], "postgresql://user:pass@db.example:5432/app")
        self.assertNotIn("BACKEND_DATA_DIR", env)
        self.assertNotIn("PORT", env)


if __name__ == "__main__":
    unittest.main()

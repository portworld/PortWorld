from __future__ import annotations

import unittest
import unittest.mock as mock

from portworld_cli.azure.stages.database import (
    generate_database_password,
    resolve_database_url_from_container_app_secret,
)
from portworld_cli.azure.stages.config import ResolvedAzureDeployConfig


def _config() -> ResolvedAzureDeployConfig:
    return ResolvedAzureDeployConfig(
        runtime_source="source",
        image_source_mode="source_build",
        subscription_id="sub-1",
        tenant_id="tenant-1",
        resource_group="rg",
        region="westeurope",
        environment_name="env",
        app_name="app",
        database_url=None,
        storage_account="pwstorage123",
        blob_container="pw-artifacts",
        blob_endpoint="https://pwstorage123.blob.core.windows.net",
        acr_name="pwapp123",
        acr_server="pw.azurecr.io",
        acr_repo="app-backend",
        postgres_server_name="pwpgapp123",
        postgres_database_name="portworld",
        postgres_admin_username="pwadmin",
        image_tag="abc123",
        image_uri="pw.azurecr.io/app-backend:abc123",
        published_release_tag=None,
        published_image_ref=None,
    )


class AzureDatabaseStageTests(unittest.TestCase):
    def test_generate_database_password_prefix(self) -> None:
        value = generate_database_password()
        self.assertTrue(value.startswith("Pw-"))
        self.assertTrue(value.endswith("!"))

    def test_resolve_database_url_from_container_app_secret(self) -> None:
        adapters = mock.Mock()
        adapters.compute.run_json.return_value = mock.Mock(
            ok=True,
            value={"value": "postgresql://user:pass@db:5432/app"},
            message=None,
        )
        value = resolve_database_url_from_container_app_secret(_config(), adapters=adapters)
        self.assertEqual(value, "postgresql://user:pass@db:5432/app")


if __name__ == "__main__":
    unittest.main()

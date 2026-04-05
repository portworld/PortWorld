from __future__ import annotations

import unittest
import unittest.mock as mock

from portworld_cli.azure.stages.artifacts import ensure_storage
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


class AzureArtifactsStageTests(unittest.TestCase):
    def test_ensure_storage_creates_container_when_missing(self) -> None:
        adapters = mock.Mock()
        adapters.storage.run_json.side_effect = [
            mock.Mock(ok=True, value={"name": "pwstorage123"}, message=None),  # account show
            mock.Mock(ok=True, value=[{"value": "key"}], message=None),  # keys list
            mock.Mock(ok=True, value={"exists": False}, message=None),  # container exists
            mock.Mock(ok=True, value={"created": True}, message=None),  # container create
        ]
        stages: list[dict[str, object]] = []
        ensure_storage(_config(), stage_records=stages, adapters=adapters)
        self.assertTrue(any(stage.get("stage") == "storage_provision" for stage in stages))


if __name__ == "__main__":
    unittest.main()

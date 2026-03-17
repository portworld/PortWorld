from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.azure.doctor import evaluate_azure_container_apps_readiness
from portworld_cli.workspace.project_config import ProjectConfig


class AzureDoctorTests(unittest.TestCase):
    @mock.patch("portworld_cli.azure.doctor.azure_cli_available", return_value=False)
    def test_missing_cli_fails(self, _available: mock.Mock) -> None:
        evaluation = evaluate_azure_container_apps_readiness(
            explicit_subscription="sub-1",
            explicit_resource_group="rg",
            explicit_region="westeurope",
            explicit_environment="env",
            explicit_app="app",
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_storage_account="pwstorage123",
            explicit_blob_container="pw-artifacts",
            explicit_blob_endpoint="https://pwstorage123.blob.core.windows.net",
            env_values={},
            project_config=ProjectConfig(),
        )
        self.assertFalse(evaluation.ok)
        by_id = {check.id: check for check in evaluation.checks}
        self.assertEqual(by_id["az_cli_installed"].status, "fail")

    @mock.patch("portworld_cli.azure.doctor.run_az_json")
    @mock.patch("portworld_cli.azure.doctor.azure_cli_available", return_value=True)
    def test_valid_configuration_passes_core_checks(
        self,
        _available: mock.Mock,
        run_az_json: mock.Mock,
    ) -> None:
        run_az_json.side_effect = [
            mock.Mock(ok=True, value={"id": "sub-1", "tenantId": "tenant-1"}, message=None),
            mock.Mock(
                ok=True,
                value={"properties": {"configuration": {"ingress": {"fqdn": "app.westeurope.azurecontainerapps.io"}}}},
                message=None,
            ),
        ]
        evaluation = evaluate_azure_container_apps_readiness(
            explicit_subscription=None,
            explicit_resource_group="rg",
            explicit_region="westeurope",
            explicit_environment="env",
            explicit_app="app",
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_storage_account="pwstorage123",
            explicit_blob_container="pw-artifacts",
            explicit_blob_endpoint="https://pwstorage123.blob.core.windows.net",
            env_values={},
            project_config=ProjectConfig(),
        )
        self.assertTrue(evaluation.ok)
        by_id = {check.id: check for check in evaluation.checks}
        self.assertEqual(by_id["az_authenticated"].status, "pass")
        self.assertEqual(by_id["container_app_fqdn_present"].status, "pass")


if __name__ == "__main__":
    unittest.main()

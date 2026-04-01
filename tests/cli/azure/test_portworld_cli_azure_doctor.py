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

    @mock.patch(
        "portworld_cli.azure.doctor._container_app_checks",
        return_value=(
            [
                mock.Mock(id="container_app_fqdn_present", status="pass"),
                mock.Mock(id="container_app_ingress_external", status="pass"),
            ],
            "app.westeurope.azurecontainerapps.io",
            {
                "properties": {
                    "configuration": {
                        "ingress": {
                            "fqdn": "app.westeurope.azurecontainerapps.io",
                            "external": True,
                        }
                    },
                    "template": {
                        "containers": [
                            {
                                "env": [
                                    {"name": "BACKEND_STORAGE_BACKEND", "value": "managed"},
                                    {"name": "BACKEND_OBJECT_STORE_PROVIDER", "value": "azure_blob"},
                                    {"name": "BACKEND_OBJECT_STORE_NAME", "value": "pw-artifacts"},
                                    {
                                        "name": "BACKEND_OBJECT_STORE_ENDPOINT",
                                        "value": "https://pwstorage123.blob.core.windows.net",
                                    },
                                    {
                                        "name": "BACKEND_DATABASE_URL",
                                        "secretRef": "backend-database-url",
                                    },
                                ]
                            }
                        ]
                    },
                }
            },
        ),
    )
    @mock.patch(
        "portworld_cli.azure.doctor._container_apps_environment_exists_check",
        return_value=mock.Mock(id="azure_container_apps_environment_exists", status="pass"),
    )
    @mock.patch(
        "portworld_cli.azure.doctor._postgres_database_exists_check",
        return_value=mock.Mock(id="azure_postgres_database_exists", status="pass"),
    )
    @mock.patch(
        "portworld_cli.azure.doctor._postgres_server_exists_check",
        return_value=mock.Mock(id="azure_postgres_server_exists", status="pass"),
    )
    @mock.patch(
        "portworld_cli.azure.doctor._storage_checks",
        return_value=[
            mock.Mock(id="azure_storage_account_exists", status="pass"),
            mock.Mock(id="azure_blob_container_exists", status="pass"),
        ],
    )
    @mock.patch(
        "portworld_cli.azure.doctor._acr_exists_check",
        return_value=mock.Mock(id="azure_acr_exists", status="pass"),
    )
    @mock.patch(
        "portworld_cli.azure.doctor._resource_group_exists_check",
        return_value=mock.Mock(id="azure_resource_group_exists", status="pass"),
    )
    @mock.patch(
        "portworld_cli.azure.doctor._provider_registration_checks",
        return_value=[
            mock.Mock(id="az_provider_microsoft_app_registered", status="pass"),
            mock.Mock(id="az_provider_microsoft_containerregistry_registered", status="pass"),
            mock.Mock(id="az_provider_microsoft_storage_registered", status="pass"),
            mock.Mock(id="az_provider_microsoft_dbforpostgresql_registered", status="pass"),
        ],
    )
    @mock.patch("portworld_cli.azure.doctor.AzureAdapters.create")
    @mock.patch("portworld_cli.azure.doctor.azure_cli_available", return_value=True)
    def test_valid_configuration_passes_core_checks(
        self,
        _available: mock.Mock,
        create_adapters: mock.Mock,
        _provider_registration_checks: mock.Mock,
        _resource_group_exists_check: mock.Mock,
        _acr_exists_check: mock.Mock,
        _storage_checks: mock.Mock,
        _postgres_server_exists_check: mock.Mock,
        _postgres_database_exists_check: mock.Mock,
        _container_apps_environment_exists_check: mock.Mock,
        _container_app_checks: mock.Mock,
    ) -> None:
        adapters = mock.Mock()
        adapters.compute.run_json.side_effect = [
            mock.Mock(ok=True, value={"name": "containerapp"}, message=None),
            mock.Mock(ok=True, value={"id": "sub-1", "tenantId": "tenant-1"}, message=None),
        ]
        create_adapters.return_value = adapters
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
        self.assertEqual(by_id["az_containerapp_extension_ready"].status, "pass")
        self.assertEqual(by_id["az_provider_microsoft_app_registered"].status, "pass")
        self.assertEqual(by_id["container_app_fqdn_present"].status, "pass")
        self.assertEqual(by_id["container_app_ingress_external"].status, "pass")


if __name__ == "__main__":
    unittest.main()

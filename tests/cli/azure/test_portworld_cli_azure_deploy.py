from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import unittest
from unittest import mock

from portworld_cli.azure.deploy import (
    DeployAzureContainerAppsOptions,
    _ResolvedAzureDeployConfig,
    _run_azure_deploy_mutations,
    _split_runtime_env_for_azure,
    run_deploy_azure_container_apps,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_SOURCE_BUILD


def _base_config() -> _ResolvedAzureDeployConfig:
    return _ResolvedAzureDeployConfig(
        runtime_source="source",
        image_source_mode=IMAGE_SOURCE_MODE_SOURCE_BUILD,
        subscription_id="sub-1",
        tenant_id="tenant-1",
        resource_group="rg",
        region="westeurope",
        environment_name="env",
        app_name="app",
        database_url="postgresql://user:pass@db:5432/app",
        storage_account="pwstorage123",
        blob_container="pw-artifacts",
        blob_endpoint="https://pwstorage123.blob.core.windows.net",
        acr_server="pw.azurecr.io",
        acr_repo="app-backend",
        image_tag="abc123",
        image_uri="pw.azurecr.io/app-backend:abc123",
        cors_origins="https://app.example.com",
        allowed_hosts="app.example.com",
        published_release_tag=None,
        published_image_ref=None,
    )


class AzureDeployTests(unittest.TestCase):
    @mock.patch("portworld_cli.azure.deploy.write_deploy_state")
    @mock.patch("portworld_cli.azure.deploy._probe_ws", return_value=True)
    @mock.patch("portworld_cli.azure.deploy._probe_livez", return_value=True)
    @mock.patch("portworld_cli.azure.deploy._run_azure_deploy_mutations", return_value="app.westeurope.azurecontainerapps.io")
    @mock.patch("portworld_cli.azure.deploy._confirm_mutations")
    @mock.patch("portworld_cli.azure.deploy._resolve_azure_deploy_config")
    @mock.patch("portworld_cli.azure.deploy.load_deploy_session")
    @mock.patch("portworld_cli.azure.deploy.azure_cli_available", return_value=True)
    def test_success_runs_mutations_then_writes_state(
        self,
        _azure_cli_available: mock.Mock,
        load_session: mock.Mock,
        resolve_config: mock.Mock,
        _confirm: mock.Mock,
        run_mutations: mock.Mock,
        _probe_livez: mock.Mock,
        _probe_ws: mock.Mock,
        write_state: mock.Mock,
    ) -> None:
        session = mock.Mock()
        session.merged_env_values.return_value = {"BACKEND_BEARER_TOKEN": "token"}
        session.project_config = mock.Mock()
        session.effective_runtime_source = "source"
        session.project_paths = mock.Mock(project_root=Path("/tmp/project"))
        session.workspace_paths = mock.Mock()
        session.workspace_paths.state_file_for_target.return_value = Path("/tmp/state/azure-container-apps.json")
        load_session.return_value = session
        resolve_config.return_value = _base_config()

        result = run_deploy_azure_container_apps(
            CLIContext(project_root_override=None, verbose=False, json_output=False, non_interactive=True, yes=True),
            DeployAzureContainerAppsOptions(
                subscription=None,
                resource_group=None,
                region=None,
                environment=None,
                app=None,
                database_url=None,
                storage_account=None,
                blob_container=None,
                blob_endpoint=None,
                acr_server=None,
                acr_repo=None,
                tag=None,
                cors_origins=None,
                allowed_hosts=None,
            ),
        )

        self.assertTrue(result.ok)
        run_mutations.assert_called_once()
        write_state.assert_called_once()
        self.assertIn("stages", result.data)
        stages = result.data["stages"]
        self.assertTrue(any(stage.get("stage") == "mutation_plan" for stage in stages))

    @mock.patch("portworld_cli.azure.deploy.write_deploy_state")
    @mock.patch("portworld_cli.azure.deploy._run_azure_deploy_mutations")
    @mock.patch("portworld_cli.azure.deploy._confirm_mutations")
    @mock.patch("portworld_cli.azure.deploy._resolve_azure_deploy_config")
    @mock.patch("portworld_cli.azure.deploy.load_deploy_session")
    @mock.patch("portworld_cli.azure.deploy.azure_cli_available", return_value=True)
    def test_failure_during_mutation_does_not_write_state(
        self,
        _azure_cli_available: mock.Mock,
        load_session: mock.Mock,
        resolve_config: mock.Mock,
        _confirm: mock.Mock,
        run_mutations: mock.Mock,
        write_state: mock.Mock,
    ) -> None:
        session = mock.Mock()
        session.merged_env_values.return_value = {}
        session.project_config = mock.Mock()
        session.effective_runtime_source = "source"
        session.project_paths = mock.Mock(project_root=Path("/tmp/project"))
        session.workspace_paths = mock.Mock()
        load_session.return_value = session
        resolve_config.return_value = _base_config()
        run_mutations.side_effect = DeployStageError(
            stage="container_app_update",
            message="Unable to update Azure Container App.",
        )

        result = run_deploy_azure_container_apps(
            CLIContext(project_root_override=None, verbose=False, json_output=False, non_interactive=True, yes=True),
            DeployAzureContainerAppsOptions(
                subscription=None,
                resource_group=None,
                region=None,
                environment=None,
                app=None,
                database_url=None,
                storage_account=None,
                blob_container=None,
                blob_endpoint=None,
                acr_server=None,
                acr_repo=None,
                tag=None,
                cors_origins=None,
                allowed_hosts=None,
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("container_app_update", result.message or "")
        write_state.assert_not_called()

    @mock.patch("portworld_cli.azure.deploy._wait_for_container_app_readiness", return_value="app.westeurope.azurecontainerapps.io")
    @mock.patch("portworld_cli.azure.deploy.run_az_json")
    def test_mutations_use_secretrefs_for_sensitive_env_values(
        self,
        run_az_json: mock.Mock,
        _wait_ready: mock.Mock,
    ) -> None:
        run_az_json.side_effect = [
            mock.Mock(ok=True, value={}, message=None),  # account set
            mock.Mock(ok=True, value={"name": "app"}, message=None),  # app show
            mock.Mock(ok=True, value={"name": "app"}, message=None),  # app update
        ]
        stages: list[dict[str, object]] = []
        env_values = OrderedDict(
            {
                "OPENAI_API_KEY": "secret",
                "BACKEND_BEARER_TOKEN": "token",
                "CORS_ORIGINS": "https://app.example.com",
            }
        )
        fqdn = _run_azure_deploy_mutations(config=_base_config(), env_values=env_values, stage_records=stages)
        self.assertEqual(fqdn, "app.westeurope.azurecontainerapps.io")
        update_call = run_az_json.call_args_list[2].args[0]
        self.assertIn("--secrets", update_call)
        self.assertIn("--set-env-vars", update_call)
        self.assertTrue(any(value.startswith("BACKEND_DATABASE_URL=secretref:") for value in update_call))
        self.assertTrue(any(value.startswith("OPENAI_API_KEY=secretref:") for value in update_call))

    def test_split_runtime_env_for_azure_classifies_sensitive_keys(self) -> None:
        plain, secrets = _split_runtime_env_for_azure(
            OrderedDict(
                {
                    "BACKEND_PROFILE": "production",
                    "OPENAI_API_KEY": "secret",
                    "BACKEND_DATABASE_URL": "postgresql://user:pass@db:5432/app",
                }
            )
        )
        self.assertIn("BACKEND_PROFILE", plain)
        self.assertIn("OPENAI_API_KEY", secrets)
        self.assertIn("BACKEND_DATABASE_URL", secrets)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import unittest
from unittest import mock

from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployGCPCloudRunOptions, DeployStageError, ResolvedDeployConfig
from portworld_cli.deploy.service import run_deploy_gcp_cloud_run
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_PUBLISHED_RELEASE, IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.gcp.cloud_run import CloudRunServiceRef
from portworld_cli.gcp.types import MutationOutcome


def _base_config(*, image_source_mode: str = IMAGE_SOURCE_MODE_SOURCE_BUILD) -> ResolvedDeployConfig:
    return ResolvedDeployConfig(
        runtime_source="source" if image_source_mode == IMAGE_SOURCE_MODE_SOURCE_BUILD else "published",
        image_source_mode=image_source_mode,
        project_id="project-1",
        region="europe-west1",
        service_name="portworld-backend",
        artifact_repository_base="portworld",
        artifact_repository="portworld",
        sql_instance_name="portworld-pg",
        database_name="portworld",
        bucket_name="project-1-portworld-artifacts",
        image_tag="abc123",
        deploy_image_uri="europe-west1-docker.pkg.dev/project-1/portworld/portworld-backend:abc123",
        published_release_tag=(None if image_source_mode == IMAGE_SOURCE_MODE_SOURCE_BUILD else "v1.2.3"),
        published_image_ref=(
            None
            if image_source_mode == IMAGE_SOURCE_MODE_SOURCE_BUILD
            else "ghcr.io/portworld/portworld-backend:v1.2.3"
        ),
        min_instances=0,
        max_instances=1,
        concurrency=20,
        cpu="1",
        memory="1Gi",
    )


class GCPDeployTests(unittest.TestCase):
    def _cli_context(self) -> CLIContext:
        return CLIContext(
            project_root_override=None,
            verbose=False,
            json_output=False,
            non_interactive=True,
            yes=True,
        )

    def _session(self, *, runtime_source: str = "source") -> mock.Mock:
        session = mock.Mock()
        session.merged_env_values.return_value = {"BACKEND_BEARER_TOKEN": "token"}
        session.project_config = mock.Mock()
        session.remembered_deploy_state = {}
        session.workspace_root = Path("/tmp/project")
        session.workspace_resolution_source = "explicit"
        session.active_workspace_root = Path("/tmp/project")
        session.env_path = Path("/tmp/project/backend/.env")
        session.effective_runtime_source = runtime_source
        session.project_paths = mock.Mock(
            project_root=Path("/tmp/project"),
            dockerfile=Path("/tmp/project/backend/Dockerfile"),
        )
        session.workspace_paths = mock.Mock()
        session.workspace_paths.project_config_file = Path("/tmp/project/.portworld/project.json")
        session.workspace_paths.state_file_for_target.return_value = Path("/tmp/state/gcp-cloud-run.json")
        return session

    def _deploy_outcome(self) -> MutationOutcome[CloudRunServiceRef]:
        return MutationOutcome(
            action="updated",
            resource=CloudRunServiceRef(
                project_id="project-1",
                region="europe-west1",
                service_name="portworld-backend",
                url="https://svc.example.com",
                image="europe-west1-docker.pkg.dev/project-1/portworld/portworld-backend:abc123",
                service_account_email="svc@project-1.iam.gserviceaccount.com",
                ingress="all",
                cloudsql_connection_name="project-1:europe-west1:portworld-pg",
            ),
        )

    @mock.patch("portworld_cli.deploy.service.write_deploy_state")
    @mock.patch("portworld_cli.deploy.service._probe_liveness", return_value=True)
    @mock.patch("portworld_cli.deploy.service.stage_deploy_cloud_run_service")
    @mock.patch("portworld_cli.deploy.service.stage_validate_final_settings")
    @mock.patch("portworld_cli.deploy.service.stage_build_cloud_run_secret_bindings", return_value={"A": "secret:latest"})
    @mock.patch("portworld_cli.deploy.service.stage_build_runtime_env_vars", return_value={"BACKEND_PROFILE": "production"})
    @mock.patch("portworld_cli.deploy.service.stage_ensure_bucket_binding")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_gcs_bucket", return_value="project-1-portworld-artifacts")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_cloud_sql")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_core_secrets")
    @mock.patch("portworld_cli.deploy.service.submit_source_build")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_artifact_repository")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_runtime_service_account", return_value="svc@project-1.iam.gserviceaccount.com")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_required_apis", return_value=[mock.Mock(service_name="run.googleapis.com")])
    @mock.patch("portworld_cli.deploy.service._confirm_mutations")
    @mock.patch("portworld_cli.deploy.service.resolve_deploy_config")
    @mock.patch("portworld_cli.deploy.service._require_active_gcloud_account", return_value="dev@example.com")
    @mock.patch("portworld_cli.deploy.service.GCPAdapters.create")
    @mock.patch("portworld_cli.deploy.service.load_deploy_session")
    def test_success_source_build_runs_build_then_writes_state(
        self,
        load_session: mock.Mock,
        _create_adapters: mock.Mock,
        _require_active_gcloud_account: mock.Mock,
        resolve_config: mock.Mock,
        _confirm_mutations: mock.Mock,
        _ensure_required_apis: mock.Mock,
        _ensure_runtime_service_account: mock.Mock,
        ensure_artifact_repository: mock.Mock,
        submit_source_build: mock.Mock,
        ensure_core_secrets: mock.Mock,
        ensure_cloud_sql: mock.Mock,
        _ensure_gcs_bucket: mock.Mock,
        _ensure_bucket_binding: mock.Mock,
        _build_runtime_env_vars: mock.Mock,
        _build_cloud_run_secret_bindings: mock.Mock,
        _validate_final_settings: mock.Mock,
        stage_deploy_cloud_run_service: mock.Mock,
        _probe_liveness: mock.Mock,
        write_deploy_state: mock.Mock,
    ) -> None:
        load_session.return_value = self._session(runtime_source="source")
        resolve_config.return_value = _base_config()
        ensure_artifact_repository.return_value = mock.Mock(repository="portworld", mode="standard")
        submit_source_build.return_value = mock.Mock(
            ok=True,
            value=mock.Mock(build_id="build-1", log_url="https://example.com/build/1"),
            error=None,
        )
        ensure_core_secrets.return_value = (
            ["backend-bearer-token"],
            {"OPENAI_API_KEY": "openai-api-key"},
            {"OPENAI_API_KEY": "secret"},
            "backend-bearer-token",
            "token",
        )
        ensure_cloud_sql.return_value = (
            mock.Mock(instance_name="portworld-pg", connection_name="project-1:europe-west1:portworld-pg"),
            "backend-database-url",
            "postgresql://db",
        )
        stage_deploy_cloud_run_service.return_value = self._deploy_outcome()

        result = run_deploy_gcp_cloud_run(
            self._cli_context(),
            DeployGCPCloudRunOptions(
                project=None,
                region=None,
                service=None,
                artifact_repo=None,
                sql_instance=None,
                database=None,
                bucket=None,
                tag=None,
                min_instances=None,
                max_instances=None,
                concurrency=None,
                cpu=None,
                memory=None,
            ),
        )

        self.assertTrue(result.ok)
        submit_source_build.assert_called_once()
        write_deploy_state.assert_called_once()
        self.assertTrue(any(stage.get("stage") == "cloud_build" for stage in result.data["stages"]))

    @mock.patch("portworld_cli.deploy.service.write_deploy_state")
    @mock.patch("portworld_cli.deploy.service._probe_liveness", return_value=True)
    @mock.patch("portworld_cli.deploy.service.stage_deploy_cloud_run_service")
    @mock.patch("portworld_cli.deploy.service.stage_validate_final_settings")
    @mock.patch("portworld_cli.deploy.service.stage_build_cloud_run_secret_bindings", return_value={"A": "secret:latest"})
    @mock.patch("portworld_cli.deploy.service.stage_build_runtime_env_vars", return_value={"BACKEND_PROFILE": "production"})
    @mock.patch("portworld_cli.deploy.service.stage_ensure_bucket_binding")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_gcs_bucket", return_value="project-1-portworld-artifacts")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_cloud_sql")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_core_secrets")
    @mock.patch("portworld_cli.deploy.service.submit_source_build")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_artifact_repository")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_runtime_service_account", return_value="svc@project-1.iam.gserviceaccount.com")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_required_apis", return_value=[mock.Mock(service_name="run.googleapis.com")])
    @mock.patch("portworld_cli.deploy.service._confirm_mutations")
    @mock.patch("portworld_cli.deploy.service.resolve_deploy_config")
    @mock.patch("portworld_cli.deploy.service._require_active_gcloud_account", return_value="dev@example.com")
    @mock.patch("portworld_cli.deploy.service.GCPAdapters.create")
    @mock.patch("portworld_cli.deploy.service.load_deploy_session")
    def test_success_published_release_skips_build_and_records_published_stage(
        self,
        load_session: mock.Mock,
        _create_adapters: mock.Mock,
        _require_active_gcloud_account: mock.Mock,
        resolve_config: mock.Mock,
        _confirm_mutations: mock.Mock,
        _ensure_required_apis: mock.Mock,
        _ensure_runtime_service_account: mock.Mock,
        ensure_artifact_repository: mock.Mock,
        submit_source_build: mock.Mock,
        ensure_core_secrets: mock.Mock,
        ensure_cloud_sql: mock.Mock,
        _ensure_gcs_bucket: mock.Mock,
        _ensure_bucket_binding: mock.Mock,
        _build_runtime_env_vars: mock.Mock,
        _build_cloud_run_secret_bindings: mock.Mock,
        _validate_final_settings: mock.Mock,
        stage_deploy_cloud_run_service: mock.Mock,
        _probe_liveness: mock.Mock,
        write_deploy_state: mock.Mock,
    ) -> None:
        load_session.return_value = self._session(runtime_source="published")
        resolve_config.return_value = _base_config(image_source_mode=IMAGE_SOURCE_MODE_PUBLISHED_RELEASE)
        ensure_artifact_repository.return_value = mock.Mock(repository="portworld-published", mode="remote")
        ensure_core_secrets.return_value = (
            ["backend-bearer-token"],
            {"OPENAI_API_KEY": "openai-api-key"},
            {"OPENAI_API_KEY": "secret"},
            "backend-bearer-token",
            "token",
        )
        ensure_cloud_sql.return_value = (
            mock.Mock(instance_name="portworld-pg", connection_name="project-1:europe-west1:portworld-pg"),
            "backend-database-url",
            "postgresql://db",
        )
        stage_deploy_cloud_run_service.return_value = self._deploy_outcome()

        result = run_deploy_gcp_cloud_run(
            self._cli_context(),
            DeployGCPCloudRunOptions(
                project=None,
                region=None,
                service=None,
                artifact_repo=None,
                sql_instance=None,
                database=None,
                bucket=None,
                tag=None,
                min_instances=None,
                max_instances=None,
                concurrency=None,
                cpu=None,
                memory=None,
            ),
        )

        self.assertTrue(result.ok)
        submit_source_build.assert_not_called()
        write_deploy_state.assert_called_once()
        self.assertTrue(
            any(stage.get("stage") == "published_image_resolution" for stage in result.data["stages"])
        )

    @mock.patch("portworld_cli.deploy.service.write_deploy_state")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_required_apis")
    @mock.patch("portworld_cli.deploy.service._confirm_mutations")
    @mock.patch("portworld_cli.deploy.service.resolve_deploy_config")
    @mock.patch("portworld_cli.deploy.service._require_active_gcloud_account", return_value="dev@example.com")
    @mock.patch("portworld_cli.deploy.service.GCPAdapters.create")
    @mock.patch("portworld_cli.deploy.service.load_deploy_session")
    def test_failure_during_mutation_does_not_write_state(
        self,
        load_session: mock.Mock,
        _create_adapters: mock.Mock,
        _require_active_gcloud_account: mock.Mock,
        resolve_config: mock.Mock,
        _confirm_mutations: mock.Mock,
        ensure_required_apis: mock.Mock,
        write_deploy_state: mock.Mock,
    ) -> None:
        load_session.return_value = self._session(runtime_source="source")
        resolve_config.return_value = _base_config()
        ensure_required_apis.side_effect = DeployStageError(
            stage="api_enablement",
            message="Required GCP APIs could not be enabled.",
        )

        result = run_deploy_gcp_cloud_run(
            self._cli_context(),
            DeployGCPCloudRunOptions(
                project=None,
                region=None,
                service=None,
                artifact_repo=None,
                sql_instance=None,
                database=None,
                bucket=None,
                tag=None,
                min_instances=None,
                max_instances=None,
                concurrency=None,
                cpu=None,
                memory=None,
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["stage"], "api_enablement")
        self.assertIn("problem:", result.message or "")
        write_deploy_state.assert_not_called()

    @mock.patch("portworld_cli.deploy.service.write_deploy_state")
    @mock.patch("portworld_cli.deploy.service._probe_liveness", return_value=True)
    @mock.patch("portworld_cli.deploy.service.stage_deploy_cloud_run_service")
    @mock.patch("portworld_cli.deploy.service.stage_validate_final_settings")
    @mock.patch("portworld_cli.deploy.service.stage_build_cloud_run_secret_bindings", return_value={"A": "secret:latest"})
    @mock.patch("portworld_cli.deploy.service.stage_build_runtime_env_vars", return_value={"BACKEND_PROFILE": "production"})
    @mock.patch("portworld_cli.deploy.service.stage_ensure_bucket_binding")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_gcs_bucket", return_value="project-1-portworld-artifacts")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_cloud_sql")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_core_secrets")
    @mock.patch("portworld_cli.deploy.service.submit_source_build")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_artifact_repository")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_runtime_service_account", return_value="svc@project-1.iam.gserviceaccount.com")
    @mock.patch("portworld_cli.deploy.service.stage_ensure_required_apis", return_value=[mock.Mock(service_name="run.googleapis.com")])
    @mock.patch("portworld_cli.deploy.service._confirm_mutations")
    @mock.patch("portworld_cli.deploy.service.resolve_deploy_config")
    @mock.patch("portworld_cli.deploy.service._require_active_gcloud_account", return_value="dev@example.com")
    @mock.patch("portworld_cli.deploy.service.GCPAdapters.create")
    @mock.patch("portworld_cli.deploy.service.load_deploy_session")
    def test_state_write_failure_returns_failure_result(
        self,
        load_session: mock.Mock,
        _create_adapters: mock.Mock,
        _require_active_gcloud_account: mock.Mock,
        resolve_config: mock.Mock,
        _confirm_mutations: mock.Mock,
        _ensure_required_apis: mock.Mock,
        _ensure_runtime_service_account: mock.Mock,
        ensure_artifact_repository: mock.Mock,
        submit_source_build: mock.Mock,
        ensure_core_secrets: mock.Mock,
        ensure_cloud_sql: mock.Mock,
        _ensure_gcs_bucket: mock.Mock,
        _ensure_bucket_binding: mock.Mock,
        _build_runtime_env_vars: mock.Mock,
        _build_cloud_run_secret_bindings: mock.Mock,
        _validate_final_settings: mock.Mock,
        stage_deploy_cloud_run_service: mock.Mock,
        _probe_liveness: mock.Mock,
        write_deploy_state: mock.Mock,
    ) -> None:
        load_session.return_value = self._session(runtime_source="source")
        resolve_config.return_value = _base_config()
        ensure_artifact_repository.return_value = mock.Mock(repository="portworld", mode="standard")
        submit_source_build.return_value = mock.Mock(
            ok=True,
            value=mock.Mock(build_id="build-1", log_url="https://example.com/build/1"),
            error=None,
        )
        ensure_core_secrets.return_value = (
            ["backend-bearer-token"],
            {"OPENAI_API_KEY": "openai-api-key"},
            {"OPENAI_API_KEY": "secret"},
            "backend-bearer-token",
            "token",
        )
        ensure_cloud_sql.return_value = (
            mock.Mock(instance_name="portworld-pg", connection_name="project-1:europe-west1:portworld-pg"),
            "backend-database-url",
            "postgresql://db",
        )
        stage_deploy_cloud_run_service.return_value = self._deploy_outcome()
        write_deploy_state.side_effect = OSError("disk full")

        result = run_deploy_gcp_cloud_run(
            self._cli_context(),
            DeployGCPCloudRunOptions(
                project=None,
                region=None,
                service=None,
                artifact_repo=None,
                sql_instance=None,
                database=None,
                bucket=None,
                tag=None,
                min_instances=None,
                max_instances=None,
                concurrency=None,
                cpu=None,
                memory=None,
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["stage"], "deploy")
        self.assertIn("disk full", result.message or "")
        write_deploy_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from portworld_cli.context import CLIContext
from portworld_cli.runtime.reporting import HealthSummary, LiveServiceStatus, LocalRuntimeStatus
from portworld_cli.services.status import run_status
from portworld_cli.targets import (
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
)
from portworld_cli.workspace.project_config import SCHEMA_VERSION


class StatusCrossTargetTests(unittest.TestCase):
    def _write_project_config(self, workspace_root: Path) -> None:
        self._write_project_config_for_target(workspace_root, TARGET_GCP_CLOUD_RUN)

    def _write_project_config_for_target(
        self,
        workspace_root: Path,
        target: str,
        *,
        runtime_source: str = "source",
    ) -> None:
        provider = "gcp"
        if target == TARGET_AWS_ECS_FARGATE:
            provider = "aws"
        elif target == TARGET_AZURE_CONTAINER_APPS:
            provider = "azure"
        project_config_path = workspace_root / ".portworld" / "project.json"
        project_config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "project_mode": "managed",
            "runtime_source": runtime_source,
            "cloud_provider": provider,
            "providers": {},
            "security": {},
            "deploy": {
                "preferred_target": target,
                "gcp_cloud_run": {},
                "aws_ecs_fargate": {},
                "azure_container_apps": {},
                "published_runtime": {},
            },
        }
        project_config_path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_state(self, workspace_root: Path, target: str, payload: dict[str, object]) -> Path:
        path = workspace_root / ".portworld" / "state" / f"{target}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    @mock.patch("portworld_cli.services.status.service.build_health_summary")
    @mock.patch("portworld_cli.services.status.service.collect_local_runtime_status", return_value=None)
    @mock.patch("portworld_cli.services.status.service.collect_live_service_status")
    def test_status_includes_by_target_state_details(
        self,
        collect_live_status: mock.Mock,
        _collect_local_runtime: mock.Mock,
        build_health_summary: mock.Mock,
    ) -> None:
        collect_live_status.return_value = LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )
        build_health_summary.return_value = HealthSummary(
            source="none",
            livez="unknown",
            readyz="unknown",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            self._write_project_config(workspace_root)
            self._write_state(
                workspace_root,
                TARGET_GCP_CLOUD_RUN,
                {"service_name": "gcp-svc", "service_url": "https://gcp.example.com"},
            )
            self._write_state(
                workspace_root,
                TARGET_AWS_ECS_FARGATE,
                {"service_name": "aws-svc", "service_url": "https://aws.example.com"},
            )
            self._write_state(workspace_root, TARGET_AZURE_CONTAINER_APPS, {})

            result = run_status(
                CLIContext(
                    project_root_override=workspace_root,
                    verbose=False,
                    json_output=False,
                    non_interactive=True,
                    yes=False,
                )
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.data["active_target"], TARGET_GCP_CLOUD_RUN)
            self.assertEqual(result.data["deploy"]["source"], "state")
            self.assertEqual(
                result.data["deploy"]["last_known"]["service_url"],
                "https://gcp.example.com",
            )

            by_target = result.data["deploy"]["by_target"]
            self.assertEqual(by_target[TARGET_GCP_CLOUD_RUN]["source"], "state")
            self.assertEqual(by_target[TARGET_AWS_ECS_FARGATE]["source"], "state")
            self.assertEqual(by_target[TARGET_AZURE_CONTAINER_APPS]["source"], "none")
            self.assertEqual(
                by_target[TARGET_AWS_ECS_FARGATE]["last_known"]["service_url"],
                "https://aws.example.com",
            )
            self.assertIsNone(by_target[TARGET_AZURE_CONTAINER_APPS]["last_known"])

            self.assertCountEqual(
                result.data["state_paths"].keys(),
                (
                    TARGET_GCP_CLOUD_RUN,
                    TARGET_AWS_ECS_FARGATE,
                    TARGET_AZURE_CONTAINER_APPS,
                ),
            )

    @mock.patch("portworld_cli.services.status.service.collect_published_backend_check_config_payload")
    @mock.patch("portworld_cli.services.status.service.build_health_summary")
    @mock.patch("portworld_cli.services.status.service.collect_local_runtime_status")
    @mock.patch("portworld_cli.services.status.service.collect_live_service_status")
    def test_status_prefers_backend_node_runtime_view_for_published_workspaces(
        self,
        collect_live_status: mock.Mock,
        collect_local_runtime: mock.Mock,
        build_health_summary: mock.Mock,
        collect_published_backend_check_config_payload: mock.Mock,
    ) -> None:
        collect_live_status.return_value = LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )
        collect_local_runtime.return_value = LocalRuntimeStatus(
            available=True,
            running=False,
            container_name="portworld-backend",
            state="exited",
            health=None,
            warning=None,
        )
        build_health_summary.return_value = HealthSummary(
            source="none",
            livez="unknown",
            readyz="unknown",
        )
        collect_published_backend_check_config_payload.return_value = {
            "ok": False,
            "extension_health": {
                "runtime_prerequisites": {
                    "node_launcher_enabled_count": 1,
                    "required_binaries": ["node", "npm", "npx"],
                    "missing_binaries": ["npx"],
                    "ok": False,
                }
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            self._write_project_config_for_target(
                workspace_root,
                TARGET_GCP_CLOUD_RUN,
                runtime_source="published",
            )

            result = run_status(
                CLIContext(
                    project_root_override=workspace_root,
                    verbose=False,
                    json_output=False,
                    non_interactive=True,
                    yes=False,
                )
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["node_mcp"]["backend"]["missing_binaries"], ["npx"])
        self.assertIn("backend_node_mcp_missing_binaries: npx", result.message)

    @mock.patch("portworld_cli.services.status.service.build_health_summary")
    @mock.patch("portworld_cli.services.status.service.collect_local_runtime_status", return_value=None)
    @mock.patch("portworld_cli.services.status.service.collect_live_service_status")
    def test_status_does_not_mutate_state_files(
        self,
        collect_live_status: mock.Mock,
        _collect_local_runtime: mock.Mock,
        build_health_summary: mock.Mock,
    ) -> None:
        collect_live_status.return_value = LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )
        build_health_summary.return_value = HealthSummary(
            source="none",
            livez="unknown",
            readyz="unknown",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            self._write_project_config(workspace_root)
            gcp_path = self._write_state(
                workspace_root,
                TARGET_GCP_CLOUD_RUN,
                {"service_name": "gcp-svc"},
            )
            aws_path = self._write_state(
                workspace_root,
                TARGET_AWS_ECS_FARGATE,
                {"service_name": "aws-svc"},
            )
            azure_path = self._write_state(
                workspace_root,
                TARGET_AZURE_CONTAINER_APPS,
                {"service_name": "azure-svc"},
            )
            before = {
                str(path): path.read_text(encoding="utf-8")
                for path in (gcp_path, aws_path, azure_path)
            }

            result = run_status(
                CLIContext(
                    project_root_override=workspace_root,
                    verbose=False,
                    json_output=False,
                    non_interactive=True,
                    yes=False,
                )
            )
            self.assertTrue(result.ok)
            after = {
                str(path): path.read_text(encoding="utf-8")
                for path in (gcp_path, aws_path, azure_path)
            }
            self.assertEqual(after, before)

    @mock.patch("portworld_cli.services.status.service.build_health_summary")
    @mock.patch("portworld_cli.services.status.service.collect_local_runtime_status", return_value=None)
    @mock.patch("portworld_cli.services.status.service.collect_live_service_status")
    def test_status_uses_actual_state_source_target_when_preferred_target_state_empty(
        self,
        collect_live_status: mock.Mock,
        _collect_local_runtime: mock.Mock,
        build_health_summary: mock.Mock,
    ) -> None:
        collect_live_status.return_value = LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )
        build_health_summary.return_value = HealthSummary(source="none", livez="unknown", readyz="unknown")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            self._write_project_config_for_target(workspace_root, TARGET_AWS_ECS_FARGATE)
            self._write_state(workspace_root, TARGET_AWS_ECS_FARGATE, {})
            self._write_state(
                workspace_root,
                TARGET_GCP_CLOUD_RUN,
                {"service_name": "gcp-svc", "service_url": "https://gcp.example.com"},
            )
            result = run_status(
                CLIContext(
                    project_root_override=workspace_root,
                    verbose=False,
                    json_output=False,
                    non_interactive=True,
                    yes=False,
                )
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.data["active_target"], TARGET_GCP_CLOUD_RUN)
            self.assertEqual(result.data["deploy"]["source_target"], TARGET_GCP_CLOUD_RUN)
            self.assertEqual(result.data["deploy"]["last_known"]["service_name"], "gcp-svc")

    @mock.patch("portworld_cli.services.status.service.build_health_summary")
    @mock.patch("portworld_cli.services.status.service.collect_local_runtime_status", return_value=None)
    @mock.patch("portworld_cli.services.status.service.collect_live_service_status")
    def test_status_non_active_malformed_state_isolated_as_invalid(
        self,
        collect_live_status: mock.Mock,
        _collect_local_runtime: mock.Mock,
        build_health_summary: mock.Mock,
    ) -> None:
        collect_live_status.return_value = LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )
        build_health_summary.return_value = HealthSummary(source="none", livez="unknown", readyz="unknown")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            self._write_project_config(workspace_root)
            self._write_state(
                workspace_root,
                TARGET_GCP_CLOUD_RUN,
                {"service_name": "gcp-svc", "service_url": "https://gcp.example.com"},
            )
            malformed_path = workspace_root / ".portworld" / "state" / f"{TARGET_AWS_ECS_FARGATE}.json"
            malformed_path.parent.mkdir(parents=True, exist_ok=True)
            malformed_path.write_text("{ not-valid-json", encoding="utf-8")

            result = run_status(
                CLIContext(
                    project_root_override=workspace_root,
                    verbose=False,
                    json_output=False,
                    non_interactive=True,
                    yes=False,
                )
            )
            self.assertTrue(result.ok)
            aws_payload = result.data["deploy"]["by_target"][TARGET_AWS_ECS_FARGATE]
            self.assertEqual(aws_payload["source"], "invalid_state")
            self.assertIsNone(aws_payload["last_known"])
            self.assertIsInstance(aws_payload["state_error"], str)

    @mock.patch("portworld_cli.services.status.service.build_health_summary")
    @mock.patch("portworld_cli.services.status.service.collect_local_runtime_status", return_value=None)
    @mock.patch("portworld_cli.services.status.service.collect_live_service_status")
    def test_status_pref_target_malformed_state_falls_back_to_valid_target(
        self,
        collect_live_status: mock.Mock,
        _collect_local_runtime: mock.Mock,
        build_health_summary: mock.Mock,
    ) -> None:
        collect_live_status.return_value = LiveServiceStatus(
            attempted=False,
            status="skipped",
            warning_code=None,
            warning_message=None,
            service_exists=None,
            service_ref=None,
        )
        build_health_summary.return_value = HealthSummary(source="none", livez="unknown", readyz="unknown")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            self._write_project_config_for_target(workspace_root, TARGET_AWS_ECS_FARGATE)
            malformed_path = workspace_root / ".portworld" / "state" / f"{TARGET_AWS_ECS_FARGATE}.json"
            malformed_path.parent.mkdir(parents=True, exist_ok=True)
            malformed_path.write_text("{ not-valid-json", encoding="utf-8")
            self._write_state(
                workspace_root,
                TARGET_GCP_CLOUD_RUN,
                {"service_name": "gcp-svc", "service_url": "https://gcp.example.com"},
            )

            result = run_status(
                CLIContext(
                    project_root_override=workspace_root,
                    verbose=False,
                    json_output=False,
                    non_interactive=True,
                    yes=False,
                )
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.data["active_target"], TARGET_GCP_CLOUD_RUN)
            self.assertEqual(result.data["deploy"]["source_target"], TARGET_GCP_CLOUD_RUN)
            aws_payload = result.data["deploy"]["by_target"][TARGET_AWS_ECS_FARGATE]
            self.assertEqual(aws_payload["source"], "invalid_state")


if __name__ == "__main__":
    unittest.main()

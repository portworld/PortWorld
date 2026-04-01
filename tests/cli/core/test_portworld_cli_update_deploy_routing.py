from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult
from portworld_cli.services.cloud_contract import AWSCloudOptions, AzureCloudOptions, CloudProviderOptions, GCPCloudOptions
from portworld_cli.services.update.service import UpdateDeployOptions, run_update_deploy
from portworld_cli.targets import TARGET_AWS_ECS_FARGATE, TARGET_GCP_CLOUD_RUN


class UpdateDeployRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli_context = CLIContext(
            project_root_override=None,
            verbose=False,
            json_output=False,
            non_interactive=True,
            yes=False,
        )

    def test_rejects_cross_provider_flags_for_active_target(self) -> None:
        session = mock.Mock()
        session.active_target.return_value = TARGET_AWS_ECS_FARGATE

        with (
            mock.patch("portworld_cli.services.update.service.load_inspection_session", return_value=session),
            mock.patch("portworld_cli.services.update.service.load_workspace_session"),
        ):
            result = run_update_deploy(
                self.cli_context,
                UpdateDeployOptions(
                    cloud=CloudProviderOptions(
                        gcp=GCPCloudOptions(project="project-1"),
                        aws=AWSCloudOptions(),
                        azure=AzureCloudOptions(),
                    ),
                    tag=None,
                ),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.data.get("target"), TARGET_AWS_ECS_FARGATE)
        self.assertIn("problem:", result.message or "")
        self.assertIn("next:", result.message or "")

    def test_routes_gcp_active_target_with_prefixed_options(self) -> None:
        session = mock.Mock()
        session.active_target.return_value = TARGET_GCP_CLOUD_RUN

        with (
            mock.patch("portworld_cli.services.update.service.load_inspection_session", return_value=session),
            mock.patch("portworld_cli.services.update.service.load_workspace_session"),
            mock.patch(
                "portworld_cli.services.update.service.run_deploy_gcp_cloud_run",
                return_value=CommandResult(
                    ok=True,
                    command="portworld deploy gcp-cloud-run",
                    message="ok",
                    data={"target": TARGET_GCP_CLOUD_RUN, "service_url": "https://svc"},
                    exit_code=0,
                ),
            ) as deploy_gcp,
        ):
            result = run_update_deploy(
                self.cli_context,
                UpdateDeployOptions(
                    cloud=CloudProviderOptions(
                        gcp=GCPCloudOptions(project="project-1", region="europe-west1", service="svc"),
                        aws=AWSCloudOptions(),
                        azure=AzureCloudOptions(),
                    ),
                    tag="v1",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.data.get("target"), TARGET_GCP_CLOUD_RUN)
        self.assertEqual(result.data.get("wrapped_command"), "portworld deploy gcp-cloud-run")
        self.assertTrue(deploy_gcp.called)
        deploy_options = deploy_gcp.call_args.args[1]
        self.assertEqual(deploy_options.project, "project-1")
        self.assertEqual(deploy_options.region, "europe-west1")
        self.assertEqual(deploy_options.service, "svc")
        self.assertEqual(deploy_options.tag, "v1")


if __name__ == "__main__":
    unittest.main()

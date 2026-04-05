from __future__ import annotations

from pathlib import Path
import unittest
import unittest.mock as mock

from portworld_cli.context import CLIContext
from portworld_cli.output import CommandResult
from portworld_cli.services.init.service import InitOptions, _run_managed_deploy


def _base_options() -> InitOptions:
    return InitOptions(
        force=False,
        realtime_provider=None,
        with_vision=False,
        without_vision=False,
        vision_provider=None,
        with_tooling=False,
        without_tooling=False,
        search_provider=None,
        realtime_api_key=None,
        vision_api_key=None,
        search_api_key=None,
        backend_profile=None,
        bearer_token=None,
        generate_bearer_token=False,
        clear_bearer_token=False,
        setup_mode=None,
        project_mode="managed",
        runtime_source="published",
        local_runtime=None,
        cloud_provider="gcp",
        target="gcp-cloud-run",
        stack_name=None,
        release_tag=None,
        host_port=None,
        project=None,
        region=None,
        service=None,
        artifact_repo=None,
        sql_instance=None,
        database=None,
        bucket=None,
        min_instances=None,
        max_instances=None,
        concurrency=None,
        cpu=None,
        memory=None,
        aws_region=None,
        aws_service=None,
        aws_vpc_id=None,
        aws_subnet_ids=None,
        azure_subscription=None,
        azure_resource_group=None,
        azure_region=None,
        azure_environment=None,
        azure_app=None,
    )


class InitManagedWorkspaceHandoffTests(unittest.TestCase):
    @mock.patch("portworld_cli.services.init.service.run_deploy_gcp_cloud_run")
    def test_managed_deploy_uses_written_workspace_root(self, run_deploy: mock.Mock) -> None:
        run_deploy.return_value = CommandResult(
            ok=True,
            command="portworld deploy gcp-cloud-run",
            data={},
            exit_code=0,
        )
        cli_context = CLIContext(
            project_root_override=Path("/Users/example/repo"),
            verbose=False,
            json_output=False,
            non_interactive=False,
            yes=False,
        )
        workspace_root = Path("/Users/example/.portworld/stacks/default")

        _run_managed_deploy(
            cli_context,
            _base_options(),
            workspace_root=workspace_root,
        )

        passed_context = run_deploy.call_args.args[0]
        self.assertEqual(passed_context.project_root_override, workspace_root)
        self.assertTrue(passed_context.yes)


if __name__ == "__main__":
    unittest.main()

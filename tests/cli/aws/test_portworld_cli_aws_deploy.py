from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import unittest
from unittest import mock

from portworld_cli.aws.deploy import (
    DeployAWSECSFargateOptions,
    _ResolvedAWSDeployConfig,
    _run_aws_deploy_mutations,
    _sanitize_runtime_env_for_output,
    run_deploy_aws_ecs_fargate,
)
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_PUBLISHED_RELEASE, IMAGE_SOURCE_MODE_SOURCE_BUILD


def _base_config(*, image_source_mode: str = IMAGE_SOURCE_MODE_SOURCE_BUILD) -> _ResolvedAWSDeployConfig:
    return _ResolvedAWSDeployConfig(
        runtime_source="source",
        image_source_mode=image_source_mode,
        account_id="123456789012",
        region="us-east-1",
        cluster_name="cluster",
        service_name="service",
        vpc_id="vpc-1",
        subnet_ids=("subnet-a", "subnet-b"),
        certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
        database_url="postgresql://user:pass@db:5432/app",
        bucket_name="service-artifacts",
        alb_url="https://alb.example.com",
        ecr_repository="service-backend",
        image_tag="abc123",
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/service-backend:abc123",
        cors_origins="https://app.example.com",
        allowed_hosts="app.example.com",
        published_release_tag=None,
        published_image_ref=None,
    )


class AWSDeployTests(unittest.TestCase):
    def test_runtime_env_output_redacts_sensitive_values(self) -> None:
        payload = _sanitize_runtime_env_for_output(
            OrderedDict(
                [
                    ("BACKEND_DATABASE_URL", "postgresql://user:pass@db:5432/app"),
                    ("BACKEND_BEARER_TOKEN", "secret-token"),
                    ("OPENAI_API_KEY", "sk-test"),
                    ("CORS_ORIGINS", "https://app.example.com"),
                ]
            )
        )
        self.assertEqual(payload["BACKEND_DATABASE_URL"], "***REDACTED***")
        self.assertEqual(payload["BACKEND_BEARER_TOKEN"], "***REDACTED***")
        self.assertEqual(payload["OPENAI_API_KEY"], "***REDACTED***")
        self.assertEqual(payload["CORS_ORIGINS"], "https://app.example.com")

    @mock.patch("portworld_cli.aws.deploy.write_deploy_state")
    @mock.patch("portworld_cli.aws.deploy._probe_ws", return_value=True)
    @mock.patch("portworld_cli.aws.deploy._probe_livez", return_value=True)
    @mock.patch("portworld_cli.aws.deploy._run_aws_deploy_mutations")
    @mock.patch("portworld_cli.aws.deploy._confirm_mutations")
    @mock.patch("portworld_cli.aws.deploy._resolve_aws_deploy_config")
    @mock.patch("portworld_cli.aws.deploy.load_deploy_session")
    @mock.patch("portworld_cli.aws.deploy.aws_cli_available", return_value=True)
    def test_success_runs_mutations_then_writes_state(
        self,
        _aws_cli_available: mock.Mock,
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
        session.workspace_root = Path("/tmp/project")
        session.workspace_paths = mock.Mock()
        session.workspace_paths.state_file_for_target.return_value = Path("/tmp/state/aws-ecs-fargate.json")
        load_session.return_value = session
        resolve_config.return_value = _base_config()

        result = run_deploy_aws_ecs_fargate(
            CLIContext(project_root_override=None, verbose=False, json_output=False, non_interactive=True, yes=True),
            DeployAWSECSFargateOptions(
                region=None,
                cluster=None,
                service=None,
                vpc_id=None,
                subnet_ids=None,
                certificate_arn=None,
                database_url=None,
                bucket=None,
                alb_url=None,
                ecr_repo=None,
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

    @mock.patch("portworld_cli.aws.deploy.write_deploy_state")
    @mock.patch("portworld_cli.aws.deploy._run_aws_deploy_mutations")
    @mock.patch("portworld_cli.aws.deploy._confirm_mutations")
    @mock.patch("portworld_cli.aws.deploy._resolve_aws_deploy_config")
    @mock.patch("portworld_cli.aws.deploy.load_deploy_session")
    @mock.patch("portworld_cli.aws.deploy.aws_cli_available", return_value=True)
    def test_failure_during_mutation_does_not_write_state(
        self,
        _aws_cli_available: mock.Mock,
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
        session.workspace_root = Path("/tmp/project")
        session.workspace_paths = mock.Mock()
        load_session.return_value = session
        resolve_config.return_value = _base_config()
        run_mutations.side_effect = DeployStageError(
            stage="ecs_service_update",
            message="Unable to update ECS service.",
        )

        result = run_deploy_aws_ecs_fargate(
            CLIContext(project_root_override=None, verbose=False, json_output=False, non_interactive=True, yes=True),
            DeployAWSECSFargateOptions(
                region=None,
                cluster=None,
                service=None,
                vpc_id=None,
                subnet_ids=None,
                certificate_arn=None,
                database_url=None,
                bucket=None,
                alb_url=None,
                ecr_repo=None,
                tag=None,
                cors_origins=None,
                allowed_hosts=None,
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("ecs_service_update", result.message or "")
        write_state.assert_not_called()

    @mock.patch("portworld_cli.aws.deploy._rollout_ecs_service")
    @mock.patch("portworld_cli.aws.deploy._build_and_push_image")
    @mock.patch("portworld_cli.aws.deploy._docker_login_to_ecr")
    @mock.patch("portworld_cli.aws.deploy._ensure_ecr_repository")
    def test_run_mutations_source_build_runs_image_publish(
        self,
        ensure_repo: mock.Mock,
        docker_login: mock.Mock,
        build_push: mock.Mock,
        rollout: mock.Mock,
    ) -> None:
        stage_records: list[dict[str, object]] = []
        _run_aws_deploy_mutations(
            _base_config(image_source_mode=IMAGE_SOURCE_MODE_SOURCE_BUILD),
            env_values=OrderedDict(),
            stage_records=stage_records,
            project_root=Path("/tmp/project"),
        )
        ensure_repo.assert_called_once()
        docker_login.assert_called_once()
        build_push.assert_called_once()
        rollout.assert_called_once()

    @mock.patch("portworld_cli.aws.deploy._rollout_ecs_service")
    @mock.patch("portworld_cli.aws.deploy._build_and_push_image")
    @mock.patch("portworld_cli.aws.deploy._docker_login_to_ecr")
    @mock.patch("portworld_cli.aws.deploy._ensure_ecr_repository")
    def test_run_mutations_published_release_skips_image_publish(
        self,
        ensure_repo: mock.Mock,
        docker_login: mock.Mock,
        build_push: mock.Mock,
        rollout: mock.Mock,
    ) -> None:
        stage_records: list[dict[str, object]] = []
        _run_aws_deploy_mutations(
            _base_config(image_source_mode=IMAGE_SOURCE_MODE_PUBLISHED_RELEASE),
            env_values=OrderedDict(),
            stage_records=stage_records,
            project_root=Path("/tmp/project"),
        )
        ensure_repo.assert_called_once()
        docker_login.assert_called_once()
        build_push.assert_not_called()
        rollout.assert_called_once()
        self.assertTrue(any(stage.get("stage") == "publish_image" for stage in stage_records))


if __name__ == "__main__":
    unittest.main()

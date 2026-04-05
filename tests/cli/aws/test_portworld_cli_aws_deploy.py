from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
import unittest
import unittest.mock as mock

from portworld_cli.aws.deploy import (
    DeployAWSECSFargateOptions,
    _run_aws_deploy_mutations,
    _sanitize_runtime_env_for_output,
    run_deploy_aws_ecs_fargate,
)
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig
from portworld_cli.aws.stages.database import DatabaseResolution
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError
from portworld_cli.deploy_artifacts import IMAGE_SOURCE_MODE_PUBLISHED_RELEASE, IMAGE_SOURCE_MODE_SOURCE_BUILD
from portworld_cli.ux.progress import ProgressReporter


def _base_config(*, image_source_mode: str = IMAGE_SOURCE_MODE_SOURCE_BUILD) -> ResolvedAWSDeployConfig:
    return ResolvedAWSDeployConfig(
        runtime_source="source",
        image_source_mode=image_source_mode,
        account_id="123456789012",
        region="us-east-1",
        app_name="service",
        requested_vpc_id="vpc-1",
        requested_subnet_ids=("subnet-a", "subnet-b"),
        explicit_database_url="postgresql://user:pass@db:5432/app",
        bucket_name="service-artifacts",
        ecr_repository="service-backend",
        image_tag="abc123",
        image_uri="123456789012.dkr.ecr.us-east-1.amazonaws.com/service-backend:abc123",
        rds_instance_identifier="service-pg",
        rds_db_name="portworld",
        rds_master_username="portworld",
        rds_password_parameter_name="/portworld/service/rds-master-password",
        published_release_tag=None,
        published_image_ref=None,
    )


def _disabled_progress() -> ProgressReporter:
    return ProgressReporter(
        CLIContext(
            project_root_override=None,
            verbose=False,
            json_output=False,
            non_interactive=True,
            yes=False,
        ),
        enabled=False,
    )


class AWSDeployTests(unittest.TestCase):
    def test_runtime_env_output_redacts_sensitive_values(self) -> None:
        payload = _sanitize_runtime_env_for_output(
            OrderedDict(
                [
                    ("BACKEND_DATABASE_URL", "postgresql://user:pass@db:5432/app"),
                    ("BACKEND_BEARER_TOKEN", "secret-token"),
                    ("OPENAI_API_KEY", "sk-test"),
                ]
            )
        )
        self.assertEqual(payload["BACKEND_DATABASE_URL"], "***REDACTED***")
        self.assertEqual(payload["BACKEND_BEARER_TOKEN"], "***REDACTED***")
        self.assertEqual(payload["OPENAI_API_KEY"], "***REDACTED***")

    @mock.patch("portworld_cli.aws.deploy.write_deploy_state")
    @mock.patch("portworld_cli.aws.deploy.wait_for_public_validation", return_value=(True, True))
    @mock.patch("portworld_cli.aws.deploy._run_aws_deploy_mutations")
    @mock.patch("portworld_cli.aws.deploy._confirm_mutations")
    @mock.patch("portworld_cli.aws.deploy.resolve_aws_deploy_config")
    @mock.patch("portworld_cli.aws.deploy.load_deploy_session")
    @mock.patch("portworld_cli.aws.deploy.aws_cli_available", return_value=True)
    def test_success_runs_mutations_then_writes_state(
        self,
        _aws_cli_available: mock.Mock,
        load_session: mock.Mock,
        resolve_config: mock.Mock,
        _confirm: mock.Mock,
        run_mutations: mock.Mock,
        _wait_for_public_validation: mock.Mock,
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
                service=None,
                vpc_id=None,
                subnet_ids=None,
                database_url=None,
                bucket=None,
                ecr_repo=None,
                tag=None,
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
    @mock.patch("portworld_cli.aws.deploy.resolve_aws_deploy_config")
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
                service=None,
                vpc_id=None,
                subnet_ids=None,
                database_url=None,
                bucket=None,
                ecr_repo=None,
                tag=None,
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("ecs_service_update", result.message or "")
        self.assertIn("problem:", result.message or "")
        self.assertIn("next:", result.message or "")
        write_state.assert_not_called()

    def test_run_mutations_source_build_runs_image_publish(self) -> None:
        stage_records: list[dict[str, object]] = []
        with ExitStack() as stack:
            ensure_s3_bucket = stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_s3_bucket"))
            ensure_repo = stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecr_repository"))
            docker_login = stack.enter_context(mock.patch("portworld_cli.aws.deploy.docker_login_to_ecr"))
            build_push = stack.enter_context(mock.patch("portworld_cli.aws.deploy.build_and_push_image"))
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.resolve_or_provision_database",
                    return_value=DatabaseResolution(
                        database_url="postgresql://user:pass@db:5432/app",
                        resolved_vpc_id=None,
                        resolved_subnet_ids=(),
                        rds_security_group_id=None,
                        used_external_database=True,
                    ),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.resolve_vpc_and_subnets",
                    return_value=("vpc-1", ("subnet-a", "subnet-b")),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_service_security_groups",
                    return_value=("sg-alb", "sg-ecs"),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_application_load_balancer",
                    return_value=("alb-arn", "alb.example.com"),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_target_group",
                    return_value="tg-arn",
                )
            )
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_alb_listener"))
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_cloudfront_distribution",
                    return_value=("dist-1", "d111.cloudfront.net"),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_ecs_execution_role",
                    return_value="arn:aws:iam::123:role/exec",
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_ecs_task_role",
                    return_value="arn:aws:iam::123:role/task",
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_ecs_log_group",
                    return_value="/ecs/service",
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_ecs_cluster",
                    return_value="service-cluster",
                )
            )
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecs_service_linked_role"))
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.register_task_definition",
                    return_value="arn:aws:ecs:task-definition/service:1",
                )
            )
            upsert_service = stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.upsert_ecs_service",
                    return_value="service",
                )
            )
            wait_stable = stack.enter_context(mock.patch("portworld_cli.aws.deploy.wait_for_ecs_service_stable"))
            wait_cloudfront = stack.enter_context(mock.patch("portworld_cli.aws.deploy.wait_for_cloudfront_deployed"))

            _run_aws_deploy_mutations(
                _base_config(image_source_mode=IMAGE_SOURCE_MODE_SOURCE_BUILD),
                env_values=OrderedDict(),
                stage_records=stage_records,
                project_root=Path("/tmp/project"),
                progress=_disabled_progress(),
            )

        ensure_s3_bucket.assert_called_once()
        ensure_repo.assert_called_once()
        docker_login.assert_called_once()
        build_push.assert_called_once()
        upsert_service.assert_called_once()
        wait_stable.assert_called_once()
        wait_cloudfront.assert_called_once()

    def test_run_mutations_published_release_skips_image_publish(self) -> None:
        stage_records: list[dict[str, object]] = []
        with ExitStack() as stack:
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_s3_bucket"))
            ensure_repo = stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecr_repository"))
            docker_login = stack.enter_context(mock.patch("portworld_cli.aws.deploy.docker_login_to_ecr"))
            build_push = stack.enter_context(mock.patch("portworld_cli.aws.deploy.build_and_push_image"))
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.resolve_or_provision_database",
                    return_value=DatabaseResolution(
                        database_url="postgresql://user:pass@db:5432/app",
                        resolved_vpc_id=None,
                        resolved_subnet_ids=(),
                        rds_security_group_id=None,
                        used_external_database=True,
                    ),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.resolve_vpc_and_subnets",
                    return_value=("vpc-1", ("subnet-a", "subnet-b")),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_service_security_groups",
                    return_value=("sg-alb", "sg-ecs"),
                )
            )
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_application_load_balancer",
                    return_value=("alb-arn", "alb.example.com"),
                )
            )
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_target_group", return_value="tg-arn"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_alb_listener"))
            stack.enter_context(
                mock.patch(
                    "portworld_cli.aws.deploy.ensure_cloudfront_distribution",
                    return_value=("dist-1", "d111.cloudfront.net"),
                )
            )
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecs_execution_role", return_value="arn:aws:iam::123:role/exec"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecs_task_role", return_value="arn:aws:iam::123:role/task"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecs_log_group", return_value="/ecs/service"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecs_cluster", return_value="service-cluster"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.ensure_ecs_service_linked_role"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.register_task_definition", return_value="arn:aws:ecs:task-definition/service:1"))
            upsert_service = stack.enter_context(mock.patch("portworld_cli.aws.deploy.upsert_ecs_service", return_value="service"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.wait_for_ecs_service_stable"))
            stack.enter_context(mock.patch("portworld_cli.aws.deploy.wait_for_cloudfront_deployed"))

            _run_aws_deploy_mutations(
                _base_config(image_source_mode=IMAGE_SOURCE_MODE_PUBLISHED_RELEASE),
                env_values=OrderedDict(),
                stage_records=stage_records,
                project_root=Path("/tmp/project"),
                progress=_disabled_progress(),
            )

        ensure_repo.assert_not_called()
        docker_login.assert_not_called()
        build_push.assert_not_called()
        upsert_service.assert_called_once()
        self.assertTrue(any(stage.get("stage") == "publish_image" for stage in stage_records))


if __name__ == "__main__":
    unittest.main()

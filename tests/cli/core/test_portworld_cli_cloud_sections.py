from __future__ import annotations

from types import SimpleNamespace
import unittest

from portworld_cli.context import CLIContext
from portworld_cli.services.config.errors import ConfigValidationError
from portworld_cli.services.config.sections import apply_cloud_section, collect_cloud_section
from portworld_cli.services.config.types import CloudEditOptions
from portworld_cli.workspace.project_config import (
    CLOUD_PROVIDER_AWS,
    CLOUD_PROVIDER_GCP,
    PROJECT_MODE_LOCAL,
    PROJECT_MODE_MANAGED,
    ProjectConfig,
)


class CloudSectionTests(unittest.TestCase):
    def _session(self, config: ProjectConfig) -> SimpleNamespace:
        return SimpleNamespace(
            cli_context=CLIContext(
                project_root_override=None,
                verbose=False,
                json_output=False,
                non_interactive=True,
                yes=True,
            ),
            effective_runtime_source="source",
            project_config=config,
        )

    def test_collect_cloud_section_aws_target_and_placeholders(self) -> None:
        config = ProjectConfig(runtime_source="source")
        result = collect_cloud_section(
            self._session(config),
            CloudEditOptions(
                project_mode=PROJECT_MODE_MANAGED,
                runtime_source="source",
                cloud_provider=CLOUD_PROVIDER_AWS,
                target="aws-ecs-fargate",
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
                aws_region="us-east-1",
                aws_cluster="portworld-cluster",
                aws_service="portworld-service",
                aws_vpc_id="vpc-12345",
                aws_subnet_ids="subnet-a, subnet-b",
                azure_subscription=None,
                azure_resource_group=None,
                azure_region=None,
                azure_environment=None,
                azure_app=None,
            ),
            prompt_defaults_when_local=False,
        )
        self.assertEqual(result.cloud_provider, CLOUD_PROVIDER_AWS)
        self.assertEqual(result.preferred_target, "aws-ecs-fargate")
        self.assertEqual(result.aws_ecs_fargate.region, "us-east-1")
        self.assertEqual(result.aws_ecs_fargate.subnet_ids, ("subnet-a", "subnet-b"))

    def test_collect_cloud_section_rejects_provider_target_mismatch(self) -> None:
        config = ProjectConfig(runtime_source="source")
        with self.assertRaises(ConfigValidationError):
            collect_cloud_section(
                self._session(config),
                CloudEditOptions(
                    project_mode=PROJECT_MODE_MANAGED,
                    runtime_source="source",
                    cloud_provider=CLOUD_PROVIDER_GCP,
                    target="aws-ecs-fargate",
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
                    aws_cluster=None,
                    aws_service=None,
                    aws_vpc_id=None,
                    aws_subnet_ids=None,
                    azure_subscription=None,
                    azure_resource_group=None,
                    azure_region=None,
                    azure_environment=None,
                    azure_app=None,
                ),
                prompt_defaults_when_local=False,
            )

    def test_apply_cloud_section_local_clears_provider_and_target(self) -> None:
        config = ProjectConfig(runtime_source="source")
        result = collect_cloud_section(
            self._session(config),
            CloudEditOptions(
                project_mode=PROJECT_MODE_LOCAL,
                runtime_source="source",
                cloud_provider=CLOUD_PROVIDER_AWS,
                target="gcp-cloud-run",
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
                aws_cluster=None,
                aws_service=None,
                aws_vpc_id=None,
                aws_subnet_ids=None,
                azure_subscription=None,
                azure_resource_group=None,
                azure_region=None,
                azure_environment=None,
                azure_app=None,
            ),
            prompt_defaults_when_local=False,
        )
        updated, _ = apply_cloud_section(config, result)
        self.assertEqual(updated.project_mode, PROJECT_MODE_LOCAL)
        self.assertIsNone(updated.cloud_provider)
        self.assertIsNone(updated.deploy.preferred_target)


if __name__ == "__main__":
    unittest.main()

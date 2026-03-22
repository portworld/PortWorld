from __future__ import annotations

import unittest

from portworld_cli.targets import TARGET_AWS_ECS_FARGATE, TARGET_AZURE_CONTAINER_APPS
from portworld_cli.workspace.project_config import (
    CLOUD_PROVIDER_AWS,
    CLOUD_PROVIDER_AZURE,
    GCP_CLOUD_RUN_TARGET,
    ProjectConfigTypeError,
    ProjectConfig,
    SCHEMA_VERSION,
)


class ProjectConfigV4Tests(unittest.TestCase):
    def test_preferred_target_infers_cloud_provider_when_missing(self) -> None:
        payload = {
            "schema_version": 4,
            "project_mode": "managed",
            "runtime_source": "source",
            "providers": {},
            "security": {},
            "deploy": {
                "preferred_target": TARGET_AWS_ECS_FARGATE,
                "gcp_cloud_run": {},
                "aws_ecs_fargate": {"region": "us-east-1"},
                "azure_container_apps": {},
                "published_runtime": {},
            },
        }

        config = ProjectConfig.from_payload(payload)
        self.assertEqual(config.cloud_provider, CLOUD_PROVIDER_AWS)
        self.assertEqual(config.deploy.preferred_target, TARGET_AWS_ECS_FARGATE)
        self.assertEqual(config.deploy.aws_ecs_fargate.region, "us-east-1")

    def test_local_mode_clears_cloud_provider_and_target(self) -> None:
        payload = {
            "schema_version": 4,
            "project_mode": "local",
            "runtime_source": "source",
            "cloud_provider": CLOUD_PROVIDER_AWS,
            "providers": {},
            "security": {},
            "deploy": {
                "preferred_target": TARGET_AWS_ECS_FARGATE,
                "gcp_cloud_run": {},
                "aws_ecs_fargate": {},
                "azure_container_apps": {},
                "published_runtime": {},
            },
        }
        config = ProjectConfig.from_payload(payload)
        self.assertIsNone(config.cloud_provider)
        self.assertIsNone(config.deploy.preferred_target)

    def test_managed_mode_derives_target_from_provider(self) -> None:
        payload = {
            "schema_version": 4,
            "project_mode": "managed",
            "runtime_source": "source",
            "cloud_provider": CLOUD_PROVIDER_AZURE,
            "providers": {},
            "security": {},
            "deploy": {
                "gcp_cloud_run": {},
                "aws_ecs_fargate": {},
                "azure_container_apps": {"region": "westeurope"},
                "published_runtime": {},
            },
        }
        config = ProjectConfig.from_payload(payload)
        self.assertEqual(config.cloud_provider, CLOUD_PROVIDER_AZURE)
        self.assertEqual(config.deploy.preferred_target, TARGET_AZURE_CONTAINER_APPS)

    def test_managed_mode_rejects_provider_target_mismatch(self) -> None:
        payload = {
            "schema_version": 4,
            "project_mode": "managed",
            "runtime_source": "source",
            "cloud_provider": CLOUD_PROVIDER_AWS,
            "providers": {},
            "security": {},
            "deploy": {
                "preferred_target": TARGET_AZURE_CONTAINER_APPS,
                "gcp_cloud_run": {},
                "aws_ecs_fargate": {},
                "azure_container_apps": {},
                "published_runtime": {},
            },
        }
        with self.assertRaises(ProjectConfigTypeError):
            ProjectConfig.from_payload(payload)

    def test_rejects_legacy_schema_version(self) -> None:
        payload = {
            "schema_version": 3,
            "project_mode": "managed",
            "runtime_source": "source",
            "providers": {},
            "security": {},
            "deploy": {
                "gcp_cloud_run": {},
                "aws_ecs_fargate": {"region": "us-east-2"},
                "azure_container_apps": {"region": "westeurope"},
            },
        }
        with self.assertRaisesRegex(Exception, "Unsupported .* schema_version"):
            ProjectConfig.from_payload(payload)


if __name__ == "__main__":
    unittest.main()

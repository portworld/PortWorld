from __future__ import annotations

import unittest

from portworld_cli.targets import TARGET_AWS_ECS_FARGATE
from portworld_cli.workspace.project_config import (
    CLOUD_PROVIDER_AWS,
    GCP_CLOUD_RUN_TARGET,
    ProjectConfig,
    SCHEMA_VERSION,
)


class ProjectConfigV4Tests(unittest.TestCase):
    def test_schema_v3_payload_loads_and_normalizes_to_v4(self) -> None:
        payload = {
            "schema_version": 3,
            "project_mode": "managed",
            "runtime_source": "source",
            "cloud_provider": "gcp",
            "providers": {},
            "security": {},
            "deploy": {
                "preferred_target": GCP_CLOUD_RUN_TARGET,
                "gcp_cloud_run": {
                    "project_id": "example-project",
                },
            },
        }

        config = ProjectConfig.from_payload(payload)
        self.assertEqual(config.schema_version, SCHEMA_VERSION)
        self.assertEqual(config.deploy.gcp_cloud_run.project_id, "example-project")
        # New Phase 1 sections exist in normalized config shape.
        self.assertIsNone(config.deploy.aws_ecs_fargate.region)
        self.assertIsNone(config.deploy.azure_container_apps.region)

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


if __name__ == "__main__":
    unittest.main()


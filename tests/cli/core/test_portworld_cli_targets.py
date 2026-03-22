from __future__ import annotations

from pathlib import Path
import unittest

from portworld_cli.targets import (
    ManagedTargetStatePaths,
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
)


class ManagedTargetStatePathsTests(unittest.TestCase):
    def test_gcp_state_file_and_status_key(self) -> None:
        paths = ManagedTargetStatePaths(Path("/tmp/portworld/.portworld/state"))
        self.assertEqual(
            paths.file_for_target(TARGET_GCP_CLOUD_RUN),
            Path("/tmp/portworld/.portworld/state/gcp-cloud-run.json"),
        )
        self.assertEqual(
            paths.status_payload(exposed_only=True),
            {
                "gcp-cloud-run": "/tmp/portworld/.portworld/state/gcp-cloud-run.json",
            },
        )

    def test_unknown_target_is_rejected(self) -> None:
        paths = ManagedTargetStatePaths(Path("/tmp/portworld/.portworld/state"))
        with self.assertRaises(ValueError):
            paths.file_for_target("unknown-target")

    def test_aws_state_file_path_is_available(self) -> None:
        paths = ManagedTargetStatePaths(Path("/tmp/portworld/.portworld/state"))
        self.assertEqual(
            paths.file_for_target(TARGET_AWS_ECS_FARGATE),
            Path("/tmp/portworld/.portworld/state/aws-ecs-fargate.json"),
        )

    def test_azure_state_file_path_is_available(self) -> None:
        paths = ManagedTargetStatePaths(Path("/tmp/portworld/.portworld/state"))
        self.assertEqual(
            paths.file_for_target(TARGET_AZURE_CONTAINER_APPS),
            Path("/tmp/portworld/.portworld/state/azure-container-apps.json"),
        )


if __name__ == "__main__":
    unittest.main()

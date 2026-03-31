from __future__ import annotations

import unittest

from portworld_cli.aws.stages.ecs_runtime import ecs_expected_task_definition_ready


class AWSECSRuntimeStageTests(unittest.TestCase):
    def test_expected_task_definition_ready_true_when_primary_running(self) -> None:
        deployments = [
            {
                "status": "PRIMARY",
                "rolloutState": "COMPLETED",
                "taskDefinition": "arn:task:1",
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
            }
        ]
        self.assertTrue(
            ecs_expected_task_definition_ready(
                deployments,
                expected_task_definition_arn="arn:task:1",
            )
        )

    def test_expected_task_definition_ready_false_when_mismatch(self) -> None:
        deployments = [
            {
                "status": "PRIMARY",
                "rolloutState": "COMPLETED",
                "taskDefinition": "arn:task:old",
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
            }
        ]
        self.assertFalse(
            ecs_expected_task_definition_ready(
                deployments,
                expected_task_definition_arn="arn:task:new",
            )
        )


if __name__ == "__main__":
    unittest.main()

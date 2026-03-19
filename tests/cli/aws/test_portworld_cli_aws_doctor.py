from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.aws.doctor import evaluate_aws_ecs_fargate_readiness
from portworld_cli.workspace.project_config import ProjectConfig


class AWSDoctorTests(unittest.TestCase):
    @mock.patch("portworld_cli.aws.doctor.aws_cli_available", return_value=False)
    def test_missing_cli_fails(self, _available: mock.Mock) -> None:
        evaluation = evaluate_aws_ecs_fargate_readiness(
            explicit_region="us-east-1",
            explicit_cluster="cluster",
            explicit_service="service",
            explicit_vpc_id="vpc-1",
            explicit_subnet_ids="subnet-a,subnet-b",
            explicit_certificate_arn="arn:aws:acm:us-east-1:123:certificate/abc",
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_s3_bucket="aws-bucket-123",
            env_values={},
            project_config=ProjectConfig(),
        )
        self.assertFalse(evaluation.ok)
        check_ids = {check.id: check for check in evaluation.checks}
        self.assertEqual(check_ids["aws_cli_installed"].status, "fail")

    @mock.patch("portworld_cli.aws.doctor.run_aws_json")
    @mock.patch("portworld_cli.aws.doctor.aws_cli_available", return_value=True)
    def test_valid_configuration_passes_core_checks(
        self,
        _available: mock.Mock,
        run_aws_json: mock.Mock,
    ) -> None:
        run_aws_json.side_effect = [
            mock.Mock(ok=True, value={"Account": "123", "Arn": "arn:aws:iam::123:user/test"}, message=None),
            mock.Mock(
                ok=True,
                value={
                    "Subnets": [
                        {"VpcId": "vpc-1", "AvailabilityZone": "us-east-1a"},
                        {"VpcId": "vpc-1", "AvailabilityZone": "us-east-1b"},
                    ]
                },
                message=None,
            ),
            mock.Mock(ok=True, value={"Certificate": {"Status": "ISSUED"}}, message=None),
            mock.Mock(
                ok=True,
                value={
                    "services": [
                        {
                            "status": "ACTIVE",
                            "taskDefinition": "arn:aws:ecs:us-east-1:123:task-definition/service:1",
                            "networkConfiguration": {
                                "awsvpcConfiguration": {"subnets": ["subnet-a", "subnet-b"]}
                            },
                        }
                    ]
                },
                message=None,
            ),
            mock.Mock(
                ok=True,
                value={
                    "taskDefinition": {
                        "networkMode": "awsvpc",
                        "requiresCompatibilities": ["FARGATE"],
                        "executionRoleArn": "arn:aws:iam::123:role/ecsExec",
                        "taskRoleArn": "arn:aws:iam::123:role/appTask",
                    }
                },
                message=None,
            ),
        ]

        evaluation = evaluate_aws_ecs_fargate_readiness(
            explicit_region="us-east-1",
            explicit_cluster="cluster",
            explicit_service="service",
            explicit_vpc_id="vpc-1",
            explicit_subnet_ids="subnet-a,subnet-b",
            explicit_certificate_arn="arn:aws:acm:us-east-1:123:certificate/abc",
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_s3_bucket="aws-bucket-123",
            env_values={},
            project_config=ProjectConfig(),
        )

        self.assertTrue(evaluation.ok)
        by_id = {check.id: check for check in evaluation.checks}
        self.assertEqual(by_id["aws_authenticated"].status, "pass")
        self.assertEqual(by_id["subnet_vpc_validation"].status, "pass")
        self.assertEqual(by_id["acm_certificate_valid"].status, "pass")
        self.assertEqual(by_id["ecs_service_active"].status, "pass")
        self.assertEqual(by_id["ecs_task_definition_fargate_compatible"].status, "pass")
        self.assertEqual(by_id["ecs_execution_role_present"].status, "pass")


if __name__ == "__main__":
    unittest.main()

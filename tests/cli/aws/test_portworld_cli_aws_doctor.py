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
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_s3_bucket="aws-bucket-123",
            env_values={},
            project_config=ProjectConfig(),
        )
        self.assertFalse(evaluation.ok)
        check_ids = {check.id: check for check in evaluation.checks}
        self.assertEqual(check_ids["aws_cli_installed"].status, "fail")

    @mock.patch(
        "portworld_cli.aws.doctor._cloudfront_distribution_check",
        return_value=(mock.Mock(id="cloudfront_ready", status="pass"), "dist-1", "d111.cloudfront.net"),
    )
    @mock.patch(
        "portworld_cli.aws.doctor._alb_check",
        return_value=(mock.Mock(id="alb_ready", status="pass"), "alb.example.com"),
    )
    @mock.patch(
        "portworld_cli.aws.doctor._ecs_service_check",
        return_value=(mock.Mock(id="ecs_service_ready", status="pass"), "https://service.example.com"),
    )
    @mock.patch(
        "portworld_cli.aws.doctor._ecr_repository_ready_check",
        return_value=mock.Mock(id="ecr_repository_ready", status="pass"),
    )
    @mock.patch(
        "portworld_cli.aws.doctor._s3_bucket_ready_check",
        return_value=mock.Mock(id="s3_bucket_ready", status="pass"),
    )
    @mock.patch("portworld_cli.aws.doctor.run_aws_json")
    @mock.patch("portworld_cli.aws.doctor.aws_cli_available", return_value=True)
    def test_valid_configuration_passes_core_checks(
        self,
        _available: mock.Mock,
        run_aws_json: mock.Mock,
        _s3_bucket_ready_check: mock.Mock,
        _ecr_repository_ready_check: mock.Mock,
        _ecs_service_check: mock.Mock,
        _alb_check: mock.Mock,
        _cloudfront_distribution_check: mock.Mock,
    ) -> None:
        run_aws_json.side_effect = [
            mock.Mock(ok=True, value={"Account": "123", "Arn": "arn:aws:iam::123:user/test"}, message=None),
        ]

        evaluation = evaluate_aws_ecs_fargate_readiness(
            explicit_region="us-east-1",
            explicit_cluster="cluster",
            explicit_service="service",
            explicit_vpc_id="vpc-1",
            explicit_subnet_ids="subnet-a,subnet-b",
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_s3_bucket="aws-bucket-123",
            env_values={
                "BACKEND_PROFILE": "production",
                "CORS_ORIGINS": "https://app.example.com",
                "BACKEND_ALLOWED_HOSTS": "api.example.com",
                "BACKEND_STORAGE_BACKEND": "managed",
                "BACKEND_OBJECT_STORE_PROVIDER": "s3",
            },
            project_config=ProjectConfig(),
        )

        self.assertTrue(evaluation.ok)
        by_id = {check.id: check for check in evaluation.checks}
        self.assertEqual(by_id["aws_authenticated"].status, "pass")
        self.assertEqual(by_id["s3_bucket_name_valid"].status, "pass")
        self.assertEqual(by_id["s3_bucket_ready"].status, "pass")
        self.assertEqual(by_id["ecr_repository_ready"].status, "pass")
        self.assertEqual(by_id["ecs_service_ready"].status, "pass")
        self.assertEqual(by_id["alb_ready"].status, "pass")
        self.assertEqual(by_id["cloudfront_ready"].status, "pass")


if __name__ == "__main__":
    unittest.main()

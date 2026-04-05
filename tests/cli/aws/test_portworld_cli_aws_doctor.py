from __future__ import annotations

import os
import unittest
import unittest.mock as mock

from portworld_cli.aws.doctor import evaluate_aws_ecs_fargate_readiness
from portworld_cli.workspace.project_config import ProjectConfig


class AWSDoctorTests(unittest.TestCase):
    @mock.patch("portworld_cli.aws.doctor.aws_cli_available", return_value=False)
    def test_missing_cli_fails(self, _available: mock.Mock) -> None:
        evaluation = evaluate_aws_ecs_fargate_readiness(
            runtime_source="source",
            explicit_region="us-east-1",
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

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("portworld_cli.aws.doctor.aws_cli_available", return_value=True)
    @mock.patch("portworld_cli.aws.doctor.AWSAdapters.create")
    def test_region_falls_back_to_aws_cli_config_via_adapter_executor(
        self,
        create_adapters: mock.Mock,
        _available: mock.Mock,
    ) -> None:
        adapters = mock.Mock()
        adapters.compute.run_json.return_value = mock.Mock(
            ok=True,
            value={"Account": "123", "Arn": "arn:aws:iam::123:user/test"},
            message=None,
        )
        adapters.executor.run_text.return_value = mock.Mock(
            ok=True,
            value="us-west-2",
            message=None,
        )
        create_adapters.return_value = adapters

        evaluation = evaluate_aws_ecs_fargate_readiness(
            runtime_source="source",
            explicit_region=None,
            explicit_service=None,
            explicit_vpc_id=None,
            explicit_subnet_ids=None,
            explicit_database_url=None,
            explicit_s3_bucket=None,
            env_values={},
            project_config=ProjectConfig(),
        )

        by_id = {check.id: check for check in evaluation.checks}
        self.assertEqual(by_id["aws_region_selected"].status, "pass")
        self.assertEqual(evaluation.details.region, "us-west-2")
        adapters.executor.run_text.assert_called_once_with(["configure", "get", "region"])

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
    @mock.patch("portworld_cli.aws.doctor.AWSAdapters.create")
    @mock.patch("portworld_cli.aws.doctor.aws_cli_available", return_value=True)
    def test_valid_configuration_passes_core_checks(
        self,
        _available: mock.Mock,
        create_adapters: mock.Mock,
        _s3_bucket_ready_check: mock.Mock,
        _ecr_repository_ready_check: mock.Mock,
        _ecs_service_check: mock.Mock,
        _alb_check: mock.Mock,
        _cloudfront_distribution_check: mock.Mock,
    ) -> None:
        create_adapters.return_value = mock.Mock(
            compute=mock.Mock(
                run_json=mock.Mock(
                    return_value=mock.Mock(
                        ok=True,
                        value={"Account": "123", "Arn": "arn:aws:iam::123:user/test"},
                        message=None,
                    )
                )
            )
        )

        evaluation = evaluate_aws_ecs_fargate_readiness(
            runtime_source="source",
            explicit_region="us-east-1",
            explicit_service="service",
            explicit_vpc_id="vpc-1",
            explicit_subnet_ids="subnet-a,subnet-b",
            explicit_database_url="postgresql://user:pass@db:5432/app",
            explicit_s3_bucket="aws-bucket-123",
            env_values={
                "BACKEND_PROFILE": "production",
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
        create_adapters.assert_called_once()


if __name__ == "__main__":
    unittest.main()

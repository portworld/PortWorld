from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.aws.stages.artifacts import ensure_s3_bucket
from portworld_cli.aws.stages.config import ResolvedAWSDeployConfig


def _config() -> ResolvedAWSDeployConfig:
    return ResolvedAWSDeployConfig(
        runtime_source="source",
        image_source_mode="source_build",
        account_id="123456789012",
        region="us-east-1",
        app_name="service",
        requested_vpc_id=None,
        requested_subnet_ids=(),
        explicit_database_url=None,
        bucket_name="service-bucket",
        ecr_repository="repo",
        image_tag="tag",
        image_uri="uri",
        rds_instance_identifier="service-pg",
        rds_db_name="portworld",
        rds_master_username="portworld",
        rds_password_parameter_name="/portworld/service/rds-master-password",
        published_release_tag=None,
        published_image_ref=None,
    )


class AWSArtifactsStageTests(unittest.TestCase):
    @mock.patch("portworld_cli.aws.stages.artifacts.run_aws_json")
    @mock.patch("portworld_cli.aws.stages.artifacts.run_aws_text")
    def test_ensure_s3_bucket_creates_when_missing(self, run_text: mock.Mock, run_json: mock.Mock) -> None:
        run_text.return_value = mock.Mock(ok=False, message="NotFound")
        run_json.return_value = mock.Mock(ok=True, value={})
        stages: list[dict[str, object]] = []
        ensure_s3_bucket(_config(), stage_records=stages)
        self.assertTrue(any(stage.get("stage") == "s3_bucket" for stage in stages))


if __name__ == "__main__":
    unittest.main()

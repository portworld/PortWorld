from __future__ import annotations

from collections import OrderedDict
import unittest

from portworld_cli.aws.deploy import _ResolvedAWSDeployConfig, _build_runtime_env_vars


class AWSRuntimeEnvVarsTests(unittest.TestCase):
    def test_managed_s3_env_emits_canonical_and_compatibility_keys(self) -> None:
        env_values = OrderedDict(
            [
                ("BACKEND_DATA_DIR", "backend/var"),
                ("PORT", "8080"),
                ("FOO", "bar"),
            ]
        )
        config = _ResolvedAWSDeployConfig(
            runtime_source="source",
            image_source_mode="source_build",
            account_id="123456789012",
            region="us-east-1",
            app_name="service",
            requested_vpc_id="vpc-123",
            requested_subnet_ids=("subnet-a", "subnet-b"),
            explicit_database_url="postgresql://user:pass@db.example:5432/app",
            bucket_name="aws-managed-bucket",
            ecr_repository="repo",
            image_tag="abc123",
            image_uri="123.dkr.ecr.us-east-1.amazonaws.com/repo:abc123",
            cors_origins="https://app.example.com",
            allowed_hosts="api.example.com",
            rds_instance_identifier="service-pg",
            rds_db_name="portworld",
            rds_master_username="portworld",
            rds_password_parameter_name="/portworld/service/rds-master-password",
            published_release_tag=None,
            published_image_ref=None,
        )

        env = _build_runtime_env_vars(
            env_values,
            config,
            database_url="postgresql://user:pass@db.example:5432/app",
        )
        self.assertEqual(env["BACKEND_STORAGE_BACKEND"], "managed")
        self.assertEqual(env["BACKEND_OBJECT_STORE_PROVIDER"], "s3")
        self.assertEqual(env["BACKEND_OBJECT_STORE_NAME"], "aws-managed-bucket")
        self.assertEqual(env["BACKEND_OBJECT_STORE_BUCKET"], "aws-managed-bucket")
        self.assertEqual(env["BACKEND_OBJECT_STORE_PREFIX"], "service")
        self.assertEqual(env["BACKEND_DATABASE_URL"], "postgresql://user:pass@db.example:5432/app")
        self.assertEqual(env["PORT"], "8080")
        self.assertNotIn("BACKEND_DATA_DIR", env)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from portworld_cli.services.cloud_contract import (
    AWSCloudOptions,
    AzureCloudOptions,
    CloudProviderOptions,
    GCPCloudOptions,
    to_aws_deploy_options,
    to_azure_deploy_options,
    to_gcp_deploy_options,
    validate_cloud_flag_scope_for_doctor,
    validate_cloud_flag_scope_for_update_deploy,
)
from portworld_cli.targets import TARGET_AWS_ECS_FARGATE, TARGET_AZURE_CONTAINER_APPS, TARGET_GCP_CLOUD_RUN


class CloudContractTests(unittest.TestCase):
    def test_doctor_rejects_cross_provider_flags(self) -> None:
        issue = validate_cloud_flag_scope_for_doctor(
            target=TARGET_GCP_CLOUD_RUN,
            cloud_options=CloudProviderOptions(
                gcp=GCPCloudOptions(project="project-1"),
                aws=AWSCloudOptions(region="us-east-1"),
                azure=AzureCloudOptions(),
            ),
        )
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("AWS", issue.problem)
        self.assertIn("gcp-cloud-run", issue.problem)

    def test_doctor_local_rejects_cloud_flags(self) -> None:
        issue = validate_cloud_flag_scope_for_doctor(
            target="local",
            cloud_options=CloudProviderOptions(
                gcp=GCPCloudOptions(project="project-1"),
                aws=AWSCloudOptions(),
                azure=AzureCloudOptions(),
            ),
        )
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("Cloud target options are only supported", issue.problem)

    def test_doctor_accepts_matching_provider_flags(self) -> None:
        issue = validate_cloud_flag_scope_for_doctor(
            target=TARGET_AZURE_CONTAINER_APPS,
            cloud_options=CloudProviderOptions(
                gcp=GCPCloudOptions(),
                aws=AWSCloudOptions(),
                azure=AzureCloudOptions(subscription="sub-1"),
            ),
        )
        self.assertIsNone(issue)

    def test_update_deploy_rejects_cross_provider_flags(self) -> None:
        issue = validate_cloud_flag_scope_for_update_deploy(
            active_target=TARGET_AWS_ECS_FARGATE,
            cloud_options=CloudProviderOptions(
                gcp=GCPCloudOptions(project="project-1"),
                aws=AWSCloudOptions(),
                azure=AzureCloudOptions(),
            ),
        )
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("GCP", issue.problem)
        self.assertIn("aws-ecs-fargate", issue.problem)

    def test_deploy_option_converters_map_provider_groups(self) -> None:
        cloud = CloudProviderOptions(
            gcp=GCPCloudOptions(project="project-1", region="europe-west1", service="svc"),
            aws=AWSCloudOptions(region="us-east-1", service="svc", s3_bucket="bucket-1"),
            azure=AzureCloudOptions(subscription="sub-1", resource_group="rg-1", app="app-1"),
        )

        gcp = to_gcp_deploy_options(cloud, tag="v1")
        aws = to_aws_deploy_options(cloud, tag="v1")
        azure = to_azure_deploy_options(cloud, tag="v1")

        self.assertEqual(gcp.project, "project-1")
        self.assertEqual(aws.region, "us-east-1")
        self.assertEqual(aws.bucket, "bucket-1")
        self.assertEqual(azure.subscription, "sub-1")
        self.assertEqual(azure.resource_group, "rg-1")


if __name__ == "__main__":
    unittest.main()

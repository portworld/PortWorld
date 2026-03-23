from __future__ import annotations

import unittest
from dataclasses import replace

from portworld_cli.context import CLIContext
from portworld_cli.services.doctor import DoctorOptions, run_doctor


class DoctorRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli_context = CLIContext(
            project_root_override=None,
            verbose=False,
            json_output=False,
            non_interactive=True,
            yes=False,
        )

    def _base_options(self, target: str) -> DoctorOptions:
        return DoctorOptions(
            target=target,
            full=False,
            project=None,
            region=None,
            aws_region=None,
            aws_cluster=None,
            aws_service=None,
            aws_vpc_id=None,
            aws_subnet_ids=None,
            aws_database_url=None,
            aws_s3_bucket=None,
            azure_subscription=None,
            azure_resource_group=None,
            azure_region=None,
            azure_environment=None,
            azure_app=None,
            azure_database_url=None,
            azure_storage_account=None,
            azure_blob_container=None,
            azure_blob_endpoint=None,
        )

    def test_local_target_rejects_azure_flags(self) -> None:
        options = self._base_options("local")
        options = replace(options, azure_subscription="sub-1")
        result = run_doctor(self.cli_context, options)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)

    def test_azure_target_rejects_aws_flags(self) -> None:
        options = self._base_options("azure-container-apps")
        options = replace(options, aws_region="us-east-1")
        result = run_doctor(self.cli_context, options)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)


if __name__ == "__main__":
    unittest.main()

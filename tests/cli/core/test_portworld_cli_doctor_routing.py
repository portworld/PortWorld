from __future__ import annotations

import unittest
from dataclasses import replace

from portworld_cli.context import CLIContext
from portworld_cli.services.cloud_contract import AWSCloudOptions, AzureCloudOptions, CloudProviderOptions, GCPCloudOptions
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
            cloud=CloudProviderOptions.empty(),
        )

    def test_local_target_rejects_azure_flags(self) -> None:
        options = self._base_options("local")
        options = replace(
            options,
            cloud=CloudProviderOptions(
                gcp=GCPCloudOptions(),
                aws=AWSCloudOptions(),
                azure=AzureCloudOptions(subscription="sub-1"),
            ),
        )
        result = run_doctor(self.cli_context, options)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("problem:", result.message or "")
        self.assertIn("next:", result.message or "")
        self.assertEqual(result.data.get("target"), "local")

    def test_azure_target_rejects_aws_flags(self) -> None:
        options = self._base_options("azure-container-apps")
        options = replace(
            options,
            cloud=CloudProviderOptions(
                gcp=GCPCloudOptions(),
                aws=AWSCloudOptions(region="us-east-1"),
                azure=AzureCloudOptions(),
            ),
        )
        result = run_doctor(self.cli_context, options)
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("problem:", result.message or "")
        self.assertIn("next:", result.message or "")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from click.testing import CliRunner

from portworld_cli.main import cli


class ProviderCLISurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_init_help_uses_provider_scoped_flags(self) -> None:
        result = self.runner.invoke(cli, ["init", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--realtime-provider", result.output)
        self.assertIn("--vision-provider", result.output)
        self.assertIn("--search-provider", result.output)
        self.assertIn("--realtime-api-key", result.output)
        self.assertIn("--vision-api-key", result.output)
        self.assertIn("--search-api-key", result.output)
        self.assertNotIn("--openai-api-key", result.output)
        self.assertNotIn("--vision-provider-api-key", result.output)
        self.assertNotIn("--tavily-api-key", result.output)

    def test_config_edit_providers_help_uses_provider_scoped_flags(self) -> None:
        result = self.runner.invoke(cli, ["config", "edit", "providers", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--realtime-provider", result.output)
        self.assertIn("--vision-provider", result.output)
        self.assertIn("--search-provider", result.output)
        self.assertIn("--realtime-api-key", result.output)
        self.assertIn("--vision-api-key", result.output)
        self.assertIn("--search-api-key", result.output)
        self.assertNotIn("--openai-api-key", result.output)
        self.assertNotIn("--vision-provider-api-key", result.output)
        self.assertNotIn("--tavily-api-key", result.output)

    def test_legacy_secret_flags_fail_with_migration_message(self) -> None:
        init_result = self.runner.invoke(cli, ["init", "--openai-api-key", "test-key"])
        self.assertNotEqual(init_result.exit_code, 0)
        self.assertIn("Use --realtime-api-key instead.", init_result.output)

        vision_result = self.runner.invoke(
            cli,
            ["config", "edit", "providers", "--vision-provider-api-key", "test-key"],
        )
        self.assertNotEqual(vision_result.exit_code, 0)
        self.assertIn("Use --vision-api-key instead.", vision_result.output)

        search_result = self.runner.invoke(
            cli,
            ["config", "edit", "providers", "--tavily-api-key", "test-key"],
        )
        self.assertNotEqual(search_result.exit_code, 0)
        self.assertIn("Use --search-api-key instead.", search_result.output)

    def test_extensions_group_is_exposed(self) -> None:
        result = self.runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("extensions", result.output)

    def test_extensions_help_exposes_expected_subcommands(self) -> None:
        result = self.runner.invoke(cli, ["extensions", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("list", result.output)
        self.assertIn("show", result.output)
        self.assertIn("add", result.output)
        self.assertIn("remove", result.output)
        self.assertIn("enable", result.output)
        self.assertIn("disable", result.output)
        self.assertIn("doctor", result.output)

    def test_doctor_help_uses_gcp_prefixed_cloud_flags(self) -> None:
        result = self.runner.invoke(cli, ["doctor", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--gcp-project", result.output)
        self.assertIn("--gcp-region", result.output)
        self.assertNotIn("--project TEXT", result.output)
        self.assertNotIn("--region TEXT", result.output)

    def test_update_deploy_help_uses_gcp_prefixed_cloud_flags(self) -> None:
        result = self.runner.invoke(cli, ["update", "deploy", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--gcp-project", result.output)
        self.assertIn("--gcp-region", result.output)
        self.assertIn("--gcp-service", result.output)
        self.assertIn("--gcp-artifact-repo", result.output)
        self.assertNotIn("--project TEXT", result.output)
        self.assertNotIn("--artifact-repo TEXT", result.output)


if __name__ == "__main__":
    unittest.main()

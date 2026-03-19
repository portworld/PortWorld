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


if __name__ == "__main__":
    unittest.main()

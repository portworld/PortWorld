from __future__ import annotations

import unittest

from portworld_cli.providers.catalog import resolve_provider


class ProviderCatalogAWSTests(unittest.TestCase):
    def test_aws_provider_catalog_entry_exists(self) -> None:
        provider = resolve_provider("aws")
        self.assertIsNotNone(provider)
        assert provider is not None
        self.assertEqual(provider.id, "aws")
        self.assertEqual(provider.kind, "cloud")
        self.assertIn("aws-ecs-fargate", provider.aliases)
        self.assertIn("deploy", provider.capability_tags)
        self.assertIn("logs", provider.capability_tags)
        self.assertIn("update_deploy", provider.capability_tags)


if __name__ == "__main__":
    unittest.main()

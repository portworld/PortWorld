from __future__ import annotations

import unittest

from portworld_cli.providers.catalog import resolve_provider


class ProviderCatalogAzureTests(unittest.TestCase):
    def test_azure_provider_catalog_entry_exists(self) -> None:
        provider = resolve_provider("azure")
        self.assertIsNotNone(provider)
        assert provider is not None
        self.assertEqual(provider.id, "azure")
        self.assertEqual(provider.kind, "cloud")
        self.assertIn("azure-container-apps", provider.aliases)
        self.assertIn("deploy", provider.capability_tags)
        self.assertNotIn("logs", provider.capability_tags)
        self.assertNotIn("update_deploy", provider.capability_tags)


if __name__ == "__main__":
    unittest.main()

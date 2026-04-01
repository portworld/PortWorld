from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.azure.client import AzureAdapters


class AzureAdaptersTests(unittest.TestCase):
    def test_create_exposes_grouped_adapters(self) -> None:
        adapters = AzureAdapters.create()
        self.assertIsNotNone(adapters.image)
        self.assertIsNotNone(adapters.storage)
        self.assertIsNotNone(adapters.network)
        self.assertIsNotNone(adapters.database)
        self.assertIsNotNone(adapters.compute)
        self.assertIsNotNone(adapters.logging)

    def test_group_adapter_delegates_to_executor(self) -> None:
        executor = mock.Mock()
        executor.run_json.return_value = mock.Mock(ok=True, value={})
        adapters = AzureAdapters.create(executor=executor)
        adapters.compute.run_json(["account", "show"])
        executor.run_json.assert_called_once_with(["account", "show"])


if __name__ == "__main__":
    unittest.main()

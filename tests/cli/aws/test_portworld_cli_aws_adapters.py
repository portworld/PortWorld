from __future__ import annotations

import unittest
import unittest.mock as mock

from portworld_cli.aws.client import AWSAdapters


class AWSAdaptersTests(unittest.TestCase):
    def test_create_exposes_grouped_adapters(self) -> None:
        adapters = AWSAdapters.create()
        self.assertIsNotNone(adapters.storage)
        self.assertIsNotNone(adapters.image)
        self.assertIsNotNone(adapters.network)
        self.assertIsNotNone(adapters.database)
        self.assertIsNotNone(adapters.compute)
        self.assertIsNotNone(adapters.logging)

    def test_group_adapter_delegates_to_executor(self) -> None:
        executor = mock.Mock()
        executor.run_json.return_value = mock.Mock(ok=True, value={})
        adapters = AWSAdapters.create(executor=executor)
        adapters.compute.run_json(["sts", "get-caller-identity"])
        executor.run_json.assert_called_once_with(["sts", "get-caller-identity"])


if __name__ == "__main__":
    unittest.main()

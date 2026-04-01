from __future__ import annotations

import unittest

from portworld_cli.azure.stages.config import first_non_empty
from portworld_cli.azure.stages.shared import build_acr_name, build_postgres_server_name, build_storage_account_name


class AzureConfigStageTests(unittest.TestCase):
    def test_first_non_empty(self) -> None:
        self.assertEqual(first_non_empty(" ", "value"), "value")
        self.assertIsNone(first_non_empty(None, " "))

    def test_name_builders_apply_limits(self) -> None:
        app_name = "PortWorld-Backend-API"
        suffix = "123abc"
        self.assertLessEqual(len(build_storage_account_name(app_name, suffix)), 24)
        self.assertLessEqual(len(build_acr_name(app_name, suffix)), 50)
        self.assertLessEqual(len(build_postgres_server_name(app_name, suffix)), 63)


if __name__ == "__main__":
    unittest.main()

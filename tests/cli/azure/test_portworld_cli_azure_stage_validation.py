from __future__ import annotations

import unittest
from unittest import mock

from portworld_cli.azure.stages.validation import parse_http_status_code, probe_livez


class AzureValidationStageTests(unittest.TestCase):
    def test_parse_http_status_code(self) -> None:
        self.assertEqual(parse_http_status_code("HTTP/1.1 101 Switching Protocols\r\n"), 101)
        self.assertIsNone(parse_http_status_code(""))

    @mock.patch("portworld_cli.azure.stages.validation.httpx.get")
    def test_probe_livez_success(self, http_get: mock.Mock) -> None:
        http_get.return_value = mock.Mock(status_code=200)
        self.assertTrue(probe_livez("https://example.com"))


if __name__ == "__main__":
    unittest.main()

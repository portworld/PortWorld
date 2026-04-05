from __future__ import annotations

import unittest
import unittest.mock as mock

from portworld_cli.aws.stages.validation import parse_http_status_code, wait_for_public_validation


class AWSValidationStageTests(unittest.TestCase):
    def test_parse_http_status_code(self) -> None:
        self.assertEqual(parse_http_status_code("HTTP/1.1 101 Switching Protocols\r\n"), 101)
        self.assertIsNone(parse_http_status_code(""))

    @mock.patch("portworld_cli.aws.stages.validation.probe_ws", return_value=True)
    @mock.patch("portworld_cli.aws.stages.validation.probe_livez", return_value=True)
    def test_wait_for_public_validation_success(self, _probe_livez: mock.Mock, _probe_ws: mock.Mock) -> None:
        livez_ok, ws_ok = wait_for_public_validation("https://example.com", "")
        self.assertTrue(livez_ok)
        self.assertTrue(ws_ok)


if __name__ == "__main__":
    unittest.main()

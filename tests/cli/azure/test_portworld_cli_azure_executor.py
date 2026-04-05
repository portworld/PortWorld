from __future__ import annotations

import unittest
import unittest.mock as mock

from portworld_cli.azure.executor import AzureExecutor


class AzureExecutorTests(unittest.TestCase):
    @mock.patch("portworld_cli.azure.executor.subprocess.run")
    def test_run_json_parses_valid_payload(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(returncode=0, stdout='{"ok": true}', stderr="")
        result = AzureExecutor().run_json(["account", "show"])
        self.assertTrue(result.ok)
        self.assertEqual(result.value, {"ok": True})

    @mock.patch("portworld_cli.azure.executor.subprocess.run")
    def test_run_json_returns_error_for_invalid_json(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(returncode=0, stdout="not-json", stderr="")
        result = AzureExecutor().run_json(["account", "show"])
        self.assertFalse(result.ok)
        self.assertIn("non-JSON", result.message or "")

    @mock.patch("portworld_cli.azure.executor.subprocess.run")
    def test_run_text_returns_stderr_on_failure(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
        result = AzureExecutor().run_text(["containerapp", "logs", "show"])
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "boom")


if __name__ == "__main__":
    unittest.main()

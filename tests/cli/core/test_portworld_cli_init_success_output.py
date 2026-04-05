from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import unittest

from portworld_cli.envfile import ParsedEnvFile
from portworld_cli.services.init.service import _bearer_token_changed, _build_final_success_message


class InitSuccessOutputTests(unittest.TestCase):
    def test_success_message_shows_backend_url_and_ios_sync(self) -> None:
        message = _build_final_success_message(
            backend_url="https://example.run.app",
            bearer_token="secret-token",
            bearer_token_changed=False,
            ios_config_synced=True,
        )

        self.assertIn("backend_url: https://example.run.app", message)
        self.assertIn("ios_config_sync: yes", message)
        self.assertNotIn("bearer_token:", message)

    def test_success_message_shows_bearer_token_only_when_changed(self) -> None:
        message = _build_final_success_message(
            backend_url="https://example.run.app",
            bearer_token="new-token",
            bearer_token_changed=True,
            ios_config_synced=False,
        )

        self.assertIn("backend_url: https://example.run.app", message)
        self.assertIn("bearer_token: new-token", message)
        self.assertNotIn("ios_config_sync: yes", message)

    def test_bearer_token_changed_detects_reused_value(self) -> None:
        session = type(
            "Session",
            (),
            {
                "existing_env": ParsedEnvFile(
                    path=Path("/tmp/backend/.env"),
                    known_values=OrderedDict({"BACKEND_BEARER_TOKEN": "same-token"}),
                    custom_overrides=OrderedDict(),
                    preserved_overrides=OrderedDict(),
                )
            },
        )()

        self.assertFalse(
            _bearer_token_changed(
                session=session,
                final_bearer_token="same-token",
            )
        )

    def test_bearer_token_changed_detects_new_value(self) -> None:
        session = type(
            "Session",
            (),
            {
                "existing_env": ParsedEnvFile(
                    path=Path("/tmp/backend/.env"),
                    known_values=OrderedDict({"BACKEND_BEARER_TOKEN": "old-token"}),
                    custom_overrides=OrderedDict(),
                    preserved_overrides=OrderedDict(),
                )
            },
        )()

        self.assertTrue(
            _bearer_token_changed(
                session=session,
                final_bearer_token="new-token",
            )
        )


if __name__ == "__main__":
    unittest.main()

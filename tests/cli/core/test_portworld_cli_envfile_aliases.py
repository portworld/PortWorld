from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import tempfile
import unittest

from portworld_cli.envfile import (
    ParsedEnvFile,
    build_canonical_env_plan,
    load_env_template_text,
    parse_env_file,
)


class EnvfileAliasTests(unittest.TestCase):
    def test_legacy_like_keys_are_preserved_as_custom_overrides(self) -> None:
        template = load_env_template_text(
            Path("/tmp/template.env"),
            "OPENAI_API_KEY=\nVISION_MISTRAL_API_KEY=\n",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("MISTRAL_API_KEY=legacy-key\n", encoding="utf-8")

            parsed = parse_env_file(env_path, template=template)

        self.assertEqual(parsed.known_values, OrderedDict())
        self.assertEqual(parsed.custom_overrides, OrderedDict([("MISTRAL_API_KEY", "legacy-key")]))
        self.assertEqual(parsed.preserved_overrides, OrderedDict([("MISTRAL_API_KEY", "legacy-key")]))

    def test_custom_legacy_like_values_stay_in_custom_overrides(self) -> None:
        template = load_env_template_text(
            Path("/tmp/template.env"),
            "OPENAI_API_KEY=\nVISION_MISTRAL_API_KEY=\n",
        )
        existing = ParsedEnvFile(
            path=Path("/tmp/.env"),
            known_values=OrderedDict([("OPENAI_API_KEY", "openai-key")]),
            custom_overrides=OrderedDict([("MISTRAL_API_KEY", "legacy-key")]),
            preserved_overrides=OrderedDict([("MISTRAL_API_KEY", "legacy-key")]),
        )

        plan = build_canonical_env_plan(template=template, existing_env=existing)

        self.assertEqual(plan.values["OPENAI_API_KEY"], "openai-key")
        self.assertEqual(plan.values["VISION_MISTRAL_API_KEY"], "")
        self.assertEqual(plan.custom_overrides, OrderedDict([("MISTRAL_API_KEY", "legacy-key")]))


if __name__ == "__main__":
    unittest.main()

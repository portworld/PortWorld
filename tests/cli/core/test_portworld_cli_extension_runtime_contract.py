from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
PUBLISHED_COMPOSE_TEMPLATE_PATH = (
    REPO_ROOT / "portworld_cli" / "templates" / "published.docker-compose.yml.template"
)
BACKEND_ENV_TEMPLATE_PATH = REPO_ROOT / "backend" / ".env.example"
PUBLISHED_ENV_TEMPLATE_PATH = (
    REPO_ROOT / "portworld_cli" / "templates" / "published.env.template"
)


class ExtensionRuntimeContractTests(unittest.TestCase):
    def test_source_compose_mounts_workspace_extensions_dir(self) -> None:
        content = SOURCE_COMPOSE_PATH.read_text(encoding="utf-8")
        self.assertIn("./.portworld:/app/.portworld:ro", content)

    def test_published_compose_template_mounts_workspace_extensions_dir(self) -> None:
        content = PUBLISHED_COMPOSE_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("./.portworld:/app/.portworld:ro", content)

    def test_backend_env_template_includes_extension_settings(self) -> None:
        content = BACKEND_ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("PORTWORLD_EXTENSIONS_MANIFEST=", content)
        self.assertIn("PORTWORLD_EXTENSIONS_PYTHON_PATH=", content)

    def test_published_env_template_includes_extension_settings(self) -> None:
        content = PUBLISHED_ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("PORTWORLD_EXTENSIONS_MANIFEST=", content)
        self.assertIn("PORTWORLD_EXTENSIONS_PYTHON_PATH=", content)


if __name__ == "__main__":
    unittest.main()

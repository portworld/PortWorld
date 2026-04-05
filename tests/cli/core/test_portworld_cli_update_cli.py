from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import unittest.mock as mock

from portworld_cli.context import CLIContext
from portworld_cli.release.lookup import ReleaseLookupResult, compare_numeric_versions
from portworld_cli.services.update.service import _detect_cli_update_mode, run_update_cli
from portworld_cli.workspace.discovery.paths import ProjectPaths


def _cli_context() -> CLIContext:
    return CLIContext(
        project_root_override=None,
        verbose=False,
        json_output=False,
        non_interactive=True,
        yes=False,
    )


class UpdateCLITests(unittest.TestCase):
    def _write_source_checkout(self, root: Path) -> ProjectPaths:
        (root / "pyproject.toml").write_text("[project]\nname='portworld'\n", encoding="utf-8")
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
        (root / "backend" / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
        (root / "backend" / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
        (root / "portworld_cli").mkdir(parents=True, exist_ok=True)
        (root / "portworld_cli" / "__init__.py").write_text("", encoding="utf-8")
        (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        return ProjectPaths.from_root(root)

    def test_detect_source_checkout_mode_from_repo_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo_paths = self._write_source_checkout(Path(temp_dir))

            mode, commands, checkout_root, release_lookup = _detect_cli_update_mode(repo_paths)

        self.assertEqual(mode, "source_checkout")
        self.assertEqual(commands, ["pipx install . --force"])
        self.assertEqual(checkout_root, repo_paths.project_root)
        self.assertEqual(release_lookup.status, "skipped")
        self.assertIsNone(release_lookup.target_version)
        self.assertIsNone(release_lookup.update_available)

    def test_detect_uv_tool_mode_recommends_upgrade_and_versioned_install(self) -> None:
        with (
            mock.patch(
                "portworld_cli.services.update.service._resolve_runtime_source_checkout_root",
                return_value=None,
            ),
            mock.patch("portworld_cli.services.update.service._detect_uv_tool_runtime", return_value=True),
            mock.patch(
                "portworld_cli.services.update.service._lookup_latest_release",
                return_value=ReleaseLookupResult(
                    status="ok",
                    target_version="v9.8.7",
                    update_available=True,
                ),
            ),
        ):
            mode, commands, checkout_root, release_lookup = _detect_cli_update_mode(repo_paths=None)

        self.assertEqual(mode, "uv_tool")
        self.assertIsNone(checkout_root)
        self.assertEqual(release_lookup.target_version, "v9.8.7")
        self.assertEqual(
            commands,
            [
                "uv tool upgrade portworld",
                'uv tool install --force "portworld==9.8.7"',
                "curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash -s -- --version v9.8.7",
            ],
        )

    def test_detect_pipx_legacy_mode_recommends_installer_and_pipx_upgrade(self) -> None:
        with (
            mock.patch(
                "portworld_cli.services.update.service._resolve_runtime_source_checkout_root",
                return_value=None,
            ),
            mock.patch("portworld_cli.services.update.service._detect_uv_tool_runtime", return_value=False),
            mock.patch("portworld_cli.services.update.service._detect_pipx_runtime", return_value=False),
            mock.patch("portworld_cli.services.update.service._detect_pipx_install", return_value=True),
            mock.patch(
                "portworld_cli.services.update.service._lookup_latest_release",
                return_value=ReleaseLookupResult(
                    status="ok",
                    target_version="v1.2.3",
                    update_available=True,
                ),
            ),
        ):
            mode, commands, _, _ = _detect_cli_update_mode(repo_paths=None)

        self.assertEqual(mode, "pipx_legacy")
        self.assertEqual(
            commands,
            [
                "curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash",
                "curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash -s -- --version v1.2.3",
                "python3 -m pipx upgrade portworld",
            ],
        )

    def test_run_update_cli_surfaces_machine_readable_fields_for_uv_tool_mode(self) -> None:
        with (
            mock.patch("portworld_cli.services.update.service._try_resolve_repo_paths", return_value=None),
            mock.patch(
                "portworld_cli.services.update.service._resolve_runtime_source_checkout_root",
                return_value=None,
            ),
            mock.patch("portworld_cli.services.update.service._detect_uv_tool_runtime", return_value=True),
            mock.patch(
                "portworld_cli.services.update.service._lookup_latest_release",
                return_value=ReleaseLookupResult(
                    status="ok",
                    target_version="v9.8.7",
                    update_available=True,
                ),
            ),
        ):
            result = run_update_cli(_cli_context())

        self.assertTrue(result.ok)
        self.assertEqual(result.data["detected_install_mode"], "uv_tool")
        self.assertEqual(result.data["target_version"], "v9.8.7")
        self.assertTrue(result.data["update_available"])
        self.assertIsNone(result.data["repo_root"])
        self.assertGreaterEqual(len(result.data["recommended_commands"]), 2)
        self.assertIn("recommended_commands:", result.message or "")

    def test_compare_numeric_versions_treats_stable_as_newer_than_same_prerelease(self) -> None:
        self.assertTrue(compare_numeric_versions("0.2.0b10", "v0.2.0"))

    def test_compare_numeric_versions_does_not_recommend_older_stable_over_newer_prerelease(self) -> None:
        self.assertFalse(compare_numeric_versions("0.2.1b3", "v0.2.0"))

    def test_compare_numeric_versions_rejects_prerelease_target_tags(self) -> None:
        self.assertIsNone(compare_numeric_versions("0.2.0b10", "v0.2.0b11"))


if __name__ == "__main__":
    unittest.main()

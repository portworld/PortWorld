from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployStageError, load_deploy_session
from portworld_cli.workspace.project_config import ProjectConfig, write_project_config


def _cli_context(project_root: Path) -> CLIContext:
    return CLIContext(
        project_root_override=project_root,
        verbose=False,
        json_output=False,
        non_interactive=True,
        yes=True,
    )


class DeploySessionRuntimeResolutionTests(unittest.TestCase):
    def _write_repo_markers(self, root: Path) -> None:
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
        (root / "backend" / ".env.example").write_text("REALTIME_PROVIDER=openai\nOPENAI_API_KEY=\n", encoding="utf-8")
        (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    def test_published_runtime_in_repo_uses_workspace_env(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_repo_markers(root)
            (root / ".env").write_text("PORT=8080\nBACKEND_BEARER_TOKEN=test-token\n", encoding="utf-8")
            write_project_config(
                root / ".portworld" / "project.json",
                ProjectConfig(runtime_source="published"),
            )

            session = load_deploy_session(_cli_context(root))

            self.assertEqual(session.effective_runtime_source, "published")
            self.assertEqual(session.env_path, (root / ".env").resolve())

    def test_published_runtime_in_repo_reports_workspace_env_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_repo_markers(root)
            write_project_config(
                root / ".portworld" / "project.json",
                ProjectConfig(runtime_source="published"),
            )

            with self.assertRaises(DeployStageError) as context:
                load_deploy_session(_cli_context(root))

            self.assertEqual(context.exception.stage, "repo_config_discovery")
            self.assertEqual(str(context.exception), "Published workspace .env is missing.")
            self.assertEqual(
                context.exception.action,
                "Run `portworld init --runtime-source published` first.",
            )


if __name__ == "__main__":
    unittest.main()

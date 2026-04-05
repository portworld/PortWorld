from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from portworld_cli.workspace.project_config import ProjectConfig, write_project_config
from portworld_cli.workspace.published import (
    DEFAULT_STACKS_DIR,
    PublishedWorkspaceError,
    PublishedWorkspaceTarget,
    load_published_env_template,
    prepare_published_workspace_root,
    resolve_published_release_ref,
    resolve_published_workspace_target,
    write_published_workspace_artifacts,
)


class PublishedWorkspaceTests(unittest.TestCase):
    def _write_source_checkout_markers(self, root: Path) -> None:
        (root / "backend").mkdir(parents=True, exist_ok=True)
        (root / "backend" / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
        (root / "backend" / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
        (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    def test_load_published_env_template_exposes_expected_keys(self) -> None:
        template = load_published_env_template()

        self.assertIn("PORT", template.ordered_keys)
        self.assertIn("BACKEND_BEARER_TOKEN", template.ordered_keys)

    def test_resolve_published_workspace_target_uses_explicit_root(self) -> None:
        target = resolve_published_workspace_target(
            explicit_root=Path("/tmp/custom-stack"),
            stack_name=None,
        )

        self.assertEqual(target.workspace_root, Path("/tmp/custom-stack").resolve())
        self.assertEqual(target.stack_name, "custom-stack")

    def test_resolve_published_workspace_target_uses_named_stack(self) -> None:
        target = resolve_published_workspace_target(
            explicit_root=None,
            stack_name="demo",
        )

        self.assertEqual(target.workspace_root, (DEFAULT_STACKS_DIR / "demo").resolve())
        self.assertEqual(target.stack_name, "demo")

    def test_resolve_published_workspace_target_uses_default_for_blank_stack(self) -> None:
        target = resolve_published_workspace_target(
            explicit_root=None,
            stack_name="   ",
        )

        self.assertEqual(target.workspace_root, (DEFAULT_STACKS_DIR / "default").resolve())
        self.assertEqual(target.stack_name, "default")

    def test_resolve_published_release_ref_accepts_explicit_tag(self) -> None:
        ref = resolve_published_release_ref("v1.2.3")

        self.assertEqual(ref.release_tag, "v1.2.3")
        self.assertIn(":v1.2.3", ref.image_ref)

    def test_resolve_published_release_ref_rejects_blank_and_invalid_tags(self) -> None:
        with self.assertRaisesRegex(PublishedWorkspaceError, "--release-tag cannot be empty"):
            resolve_published_release_ref("  ")

        with self.assertRaisesRegex(PublishedWorkspaceError, "require a concrete release tag"):
            resolve_published_release_ref("main")

    @mock.patch("portworld_cli.workspace.published._lookup_latest_release_tag", return_value="v9.8.7")
    def test_resolve_published_release_ref_looks_up_latest_tag(self, _lookup_latest_release_tag: mock.Mock) -> None:
        ref = resolve_published_release_ref("latest")

        self.assertEqual(ref.release_tag, "v9.8.7")
        self.assertIn(":v9.8.7", ref.image_ref)

    def test_prepare_published_workspace_root_rejects_source_checkout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_source_checkout_markers(root)

            with self.assertRaisesRegex(PublishedWorkspaceError, "looks like a PortWorld source checkout"):
                prepare_published_workspace_root(
                    PublishedWorkspaceTarget(workspace_root=root, stack_name="demo"),
                    force=False,
                )

    def test_prepare_published_workspace_root_rejects_nonempty_uninitialized_root_without_force(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "notes.txt").write_text("hello\n", encoding="utf-8")

            with self.assertRaisesRegex(PublishedWorkspaceError, "already exists and is not an initialized PortWorld workspace"):
                prepare_published_workspace_root(
                    PublishedWorkspaceTarget(workspace_root=root, stack_name="demo"),
                    force=False,
                )

    def test_prepare_published_workspace_root_allows_existing_initialized_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_project_config(
                root / ".portworld" / "project.json",
                ProjectConfig(runtime_source="published"),
            )

            workspace_paths = prepare_published_workspace_root(
                PublishedWorkspaceTarget(workspace_root=root, stack_name="demo"),
                force=False,
            )

        self.assertEqual(workspace_paths.workspace_root, root.resolve())

    def test_write_published_workspace_artifacts_writes_files_and_compose_backup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_paths = prepare_published_workspace_root(
                PublishedWorkspaceTarget(workspace_root=root, stack_name="demo"),
                force=True,
            )
            template = load_published_env_template()
            project_config = ProjectConfig(runtime_source="published")

            env_write_result, compose_backup_path = write_published_workspace_artifacts(
                workspace_paths=workspace_paths,
                project_config=project_config,
                env_template=template,
                env_overrides={"BACKEND_BEARER_TOKEN": "token-1"},
                compose_content="services:\n  backend:\n    image: first\n",
                force=True,
            )

            self.assertEqual(env_write_result.env_path, workspace_paths.workspace_env_file)
            self.assertIsNone(env_write_result.backup_path)
            self.assertIn("BACKEND_BEARER_TOKEN=token-1", env_write_result.content)
            self.assertIsNone(compose_backup_path)

            _, compose_backup_path = write_published_workspace_artifacts(
                workspace_paths=workspace_paths,
                project_config=project_config,
                env_template=template,
                env_overrides={"BACKEND_BEARER_TOKEN": "token-2"},
                compose_content="services:\n  backend:\n    image: second\n",
                force=False,
            )

            self.assertTrue(workspace_paths.workspace_env_file.is_file())
            self.assertTrue(workspace_paths.project_config_file.is_file())
            self.assertEqual(
                workspace_paths.compose_file.read_text(encoding="utf-8"),
                "services:\n  backend:\n    image: second\n",
            )
            assert compose_backup_path is not None
            self.assertTrue(compose_backup_path.is_file())
            self.assertEqual(
                compose_backup_path.read_text(encoding="utf-8"),
                "services:\n  backend:\n    image: first\n",
            )

    def test_write_published_workspace_artifacts_rejects_source_checkout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_source_checkout_markers(root)
            workspace_paths = mock.Mock()
            workspace_paths.source_project_paths = object()

            with self.assertRaisesRegex(PublishedWorkspaceError, "cannot target a source checkout"):
                write_published_workspace_artifacts(
                    workspace_paths=workspace_paths,
                    project_config=ProjectConfig(runtime_source="published"),
                    env_template=load_published_env_template(),
                    env_overrides={},
                    compose_content="services: {}\n",
                    force=False,
                )


if __name__ == "__main__":
    unittest.main()

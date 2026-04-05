from __future__ import annotations

import os
from pathlib import Path

from portworld_cli.workspace.session import WorkspaceSession


EXTENSIONS_MANIFEST_ENV_KEY = "PORTWORLD_EXTENSIONS_MANIFEST"
EXTENSIONS_PYTHON_PATH_ENV_KEY = "PORTWORLD_EXTENSIONS_PYTHON_PATH"


def build_extension_runtime_env_overrides(session: WorkspaceSession) -> dict[str, str]:
    manifest_path = session.workspace_paths.extensions_manifest_file
    python_path = session.workspace_paths.extensions_python_dir
    if session.project_paths is not None:
        manifest_value = _render_relative_env_value(
            env_file_dir=session.project_paths.env_file.parent,
            target=manifest_path,
        )
        python_value = _render_relative_env_value(
            env_file_dir=session.project_paths.env_file.parent,
            target=python_path,
        )
    else:
        manifest_value = _render_relative_env_value(
            env_file_dir=session.workspace_paths.workspace_env_file.parent,
            target=manifest_path,
        )
        python_value = _render_relative_env_value(
            env_file_dir=session.workspace_paths.workspace_env_file.parent,
            target=python_path,
        )
    return {
        EXTENSIONS_MANIFEST_ENV_KEY: manifest_value,
        EXTENSIONS_PYTHON_PATH_ENV_KEY: python_value,
    }


def _render_relative_env_value(*, env_file_dir: Path, target: Path) -> str:
    try:
        return os.path.relpath(target.resolve(), start=env_file_dir.resolve())
    except OSError:
        return str(target.resolve())

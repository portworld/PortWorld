from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.extensions.manifest import ExtensionManifestError, load_manifest
from portworld_cli.extensions.runtime_env import build_extension_runtime_env_overrides
from portworld_cli.workspace.session import WorkspaceSession


@dataclass(frozen=True, slots=True)
class ExtensionsSummary:
    manifest_path: str
    python_install_dir: str
    exists: bool
    installed_count: int
    enabled_count: int
    error: str | None
    runtime_env_overrides: dict[str, str]

    def to_payload(self) -> dict[str, object]:
        return {
            "manifest_path": self.manifest_path,
            "python_install_dir": self.python_install_dir,
            "exists": self.exists,
            "installed_count": self.installed_count,
            "enabled_count": self.enabled_count,
            "error": self.error,
            "runtime_env_overrides": dict(self.runtime_env_overrides),
        }


def collect_extensions_summary(session: WorkspaceSession) -> ExtensionsSummary:
    manifest_path = session.workspace_paths.extensions_manifest_file
    runtime_env_overrides = build_extension_runtime_env_overrides(session)
    try:
        manifest = load_manifest(manifest_path)
        return ExtensionsSummary(
            manifest_path=str(manifest_path),
            python_install_dir=str(session.workspace_paths.extensions_python_dir),
            exists=manifest_path.exists(),
            installed_count=len(manifest.installed),
            enabled_count=sum(1 for entry in manifest.installed if entry.enabled),
            error=None,
            runtime_env_overrides=runtime_env_overrides,
        )
    except ExtensionManifestError as exc:
        return ExtensionsSummary(
            manifest_path=str(manifest_path),
            python_install_dir=str(session.workspace_paths.extensions_python_dir),
            exists=manifest_path.exists(),
            installed_count=0,
            enabled_count=0,
            error=str(exc),
            runtime_env_overrides=runtime_env_overrides,
        )


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portworld_cli.targets import ManagedTargetStatePaths, TARGET_GCP_CLOUD_RUN


REQUIRED_REPO_MARKERS: tuple[str, ...] = (
    "backend/Dockerfile",
    "backend/.env.example",
    "docker-compose.yml",
)


class ProjectRootResolutionError(RuntimeError):
    """Raised when the PortWorld project root cannot be resolved."""


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    workspace_root: Path
    cli_dir: Path
    project_config_file: Path
    extensions_manifest_file: Path
    extensions_dir: Path
    extensions_python_dir: Path
    cli_state_dir: Path
    gcp_cloud_run_state_file: Path
    workspace_env_file: Path
    compose_file: Path
    source_project_paths: ProjectPaths | None

    @classmethod
    def from_root(cls, workspace_root: Path) -> "WorkspacePaths":
        root = workspace_root.resolve()
        source_project_paths: ProjectPaths | None = None
        candidate = ProjectPaths.from_root(root)
        if not candidate.missing_required_markers():
            source_project_paths = candidate
        return cls(
            workspace_root=root,
            cli_dir=root / ".portworld",
            project_config_file=root / ".portworld" / "project.json",
            extensions_manifest_file=root / ".portworld" / "extensions.json",
            extensions_dir=root / ".portworld" / "extensions",
            extensions_python_dir=root / ".portworld" / "extensions" / "python",
            cli_state_dir=root / ".portworld" / "state",
            gcp_cloud_run_state_file=ManagedTargetStatePaths(
                root / ".portworld" / "state"
            ).file_for_target(TARGET_GCP_CLOUD_RUN),
            workspace_env_file=root / ".env",
            compose_file=root / "docker-compose.yml",
            source_project_paths=source_project_paths,
        )

    def has_workspace_config(self) -> bool:
        return self.project_config_file.is_file()

    def has_source_checkout(self) -> bool:
        return self.source_project_paths is not None

    def require_source_project_paths(self) -> ProjectPaths:
        if self.source_project_paths is None:
            raise ProjectRootResolutionError(
                "This command requires a PortWorld source checkout with "
                "backend/Dockerfile, backend/.env.example, and docker-compose.yml."
            )
        return self.source_project_paths

    def managed_target_state_paths(self) -> ManagedTargetStatePaths:
        return ManagedTargetStatePaths(self.cli_state_dir)

    def state_file_for_target(self, target: str) -> Path:
        return self.managed_target_state_paths().file_for_target(target)

    def exposed_state_paths_payload(self) -> dict[str, str]:
        return self.managed_target_state_paths().status_payload(exposed_only=True)


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    project_root: Path
    backend_dir: Path
    env_file: Path
    env_example_file: Path
    dockerfile: Path
    compose_file: Path
    cli_dir: Path
    project_config_file: Path
    extensions_manifest_file: Path
    extensions_dir: Path
    extensions_python_dir: Path
    cli_state_dir: Path
    gcp_cloud_run_state_file: Path

    @classmethod
    def from_root(cls, project_root: Path) -> "ProjectPaths":
        root = project_root.resolve()
        return cls(
            project_root=root,
            backend_dir=root / "backend",
            env_file=root / "backend" / ".env",
            env_example_file=root / "backend" / ".env.example",
            dockerfile=root / "backend" / "Dockerfile",
            compose_file=root / "docker-compose.yml",
            cli_dir=root / ".portworld",
            project_config_file=root / ".portworld" / "project.json",
            extensions_manifest_file=root / ".portworld" / "extensions.json",
            extensions_dir=root / ".portworld" / "extensions",
            extensions_python_dir=root / ".portworld" / "extensions" / "python",
            cli_state_dir=root / ".portworld" / "state",
            gcp_cloud_run_state_file=ManagedTargetStatePaths(
                root / ".portworld" / "state"
            ).file_for_target(TARGET_GCP_CLOUD_RUN),
        )

    def missing_required_markers(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.dockerfile.is_file():
            missing.append("backend/Dockerfile")
        if not self.env_example_file.is_file():
            missing.append("backend/.env.example")
        if not self.compose_file.is_file():
            missing.append("docker-compose.yml")
        return tuple(missing)

    def validate_required_markers(self) -> None:
        missing = self.missing_required_markers()
        if missing:
            missing_list = ", ".join(missing)
            raise ProjectRootResolutionError(
                f"{self.project_root} is not a valid PortWorld project root. "
                f"Missing required files: {missing_list}."
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "project_root": str(self.project_root),
            "backend_dir": str(self.backend_dir),
            "env_file": str(self.env_file),
            "env_example_file": str(self.env_example_file),
            "dockerfile": str(self.dockerfile),
            "compose_file": str(self.compose_file),
            "cli_dir": str(self.cli_dir),
            "project_config_file": str(self.project_config_file),
            "extensions_manifest_file": str(self.extensions_manifest_file),
            "extensions_dir": str(self.extensions_dir),
            "extensions_python_dir": str(self.extensions_python_dir),
            "cli_state_dir": str(self.cli_state_dir),
            "gcp_cloud_run_state_file": str(self.gcp_cloud_run_state_file),
        }

    def managed_target_state_paths(self) -> ManagedTargetStatePaths:
        return ManagedTargetStatePaths(self.cli_state_dir)

    def state_file_for_target(self, target: str) -> Path:
        return self.managed_target_state_paths().file_for_target(target)


def resolve_project_paths(*, explicit_root: Path | None = None, start: Path | None = None) -> ProjectPaths:
    if explicit_root is not None:
        paths = ProjectPaths.from_root(explicit_root)
        paths.validate_required_markers()
        return paths

    current = (start or Path.cwd()).resolve()
    candidates = (current,) + tuple(current.parents)
    for candidate in candidates:
        paths = ProjectPaths.from_root(candidate)
        if not paths.missing_required_markers():
            return paths

    markers = ", ".join(REQUIRED_REPO_MARKERS)
    raise ProjectRootResolutionError(
        "Could not find the PortWorld project root from the current directory. "
        f"Expected to find: {markers}. Use --project-root to point at the repo root."
    )


def resolve_workspace_paths(
    *,
    explicit_root: Path | None = None,
    start: Path | None = None,
) -> WorkspacePaths:
    if explicit_root is not None:
        workspace = WorkspacePaths.from_root(explicit_root)
        if workspace.has_source_checkout() or workspace.has_workspace_config():
            return workspace
        raise ProjectRootResolutionError(
            f"{workspace.workspace_root} is not a valid PortWorld workspace. "
            "Expected either a source checkout or .portworld/project.json."
        )

    current = (start or Path.cwd()).resolve()
    candidates = (current,) + tuple(current.parents)
    for candidate in candidates:
        workspace = WorkspacePaths.from_root(candidate)
        if workspace.has_source_checkout() or workspace.has_workspace_config():
            return workspace

    raise ProjectRootResolutionError(
        "Could not find a PortWorld workspace from the current directory. "
        "Expected either a source checkout or .portworld/project.json. "
        "Use --project-root to point at the workspace root."
    )

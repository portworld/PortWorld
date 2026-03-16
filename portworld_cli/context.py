from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from portworld_cli.machine_state import load_machine_state
from portworld_cli.paths import ProjectPaths, ProjectRootResolutionError, WorkspacePaths, resolve_project_paths


WORKSPACE_RESOLUTION_SOURCE_EXPLICIT = "explicit"
WORKSPACE_RESOLUTION_SOURCE_CWD = "cwd"
WORKSPACE_RESOLUTION_SOURCE_ACTIVE = "active_workspace"


@dataclass(slots=True)
class CLIContext:
    project_root_override: Path | None
    verbose: bool
    json_output: bool
    non_interactive: bool
    yes: bool
    _resolved_project_paths: ProjectPaths | None = field(default=None, init=False, repr=False)
    _resolved_workspace_paths: WorkspacePaths | None = field(default=None, init=False, repr=False)
    _workspace_resolution_source: str | None = field(default=None, init=False, repr=False)
    _active_workspace_root: Path | None = field(default=None, init=False, repr=False)

    @property
    def workspace_resolution_source(self) -> str | None:
        return self._workspace_resolution_source

    @property
    def active_workspace_root(self) -> Path | None:
        if self._active_workspace_root is None:
            self._active_workspace_root = load_machine_state().active_workspace_root
        return self._active_workspace_root

    def resolve_project_paths(self) -> ProjectPaths:
        if self._resolved_project_paths is None:
            if self._resolved_workspace_paths is not None and self._resolved_workspace_paths.source_project_paths is not None:
                self._resolved_project_paths = self._resolved_workspace_paths.source_project_paths
            else:
                self._resolved_project_paths = resolve_project_paths(
                    explicit_root=self.project_root_override,
                )
        return self._resolved_project_paths

    def resolve_workspace_paths(self) -> WorkspacePaths:
        if self._resolved_workspace_paths is None:
            self._resolved_workspace_paths = self._resolve_workspace_paths()
        if self._resolved_workspace_paths.source_project_paths is not None:
            self._resolved_project_paths = self._resolved_workspace_paths.source_project_paths
        return self._resolved_workspace_paths

    def _resolve_workspace_paths(self) -> WorkspacePaths:
        if self.project_root_override is not None:
            workspace = WorkspacePaths.from_root(self.project_root_override)
            if workspace.has_source_checkout() or workspace.has_workspace_config():
                self._workspace_resolution_source = WORKSPACE_RESOLUTION_SOURCE_EXPLICIT
                return workspace
            raise ProjectRootResolutionError(
                f"{workspace.workspace_root} is not a valid PortWorld workspace. "
                "Expected either a source checkout or .portworld/project.json."
            )

        current = Path.cwd().resolve()
        candidates = (current,) + tuple(current.parents)
        for candidate in candidates:
            workspace = WorkspacePaths.from_root(candidate)
            if workspace.has_source_checkout() or workspace.has_workspace_config():
                self._workspace_resolution_source = WORKSPACE_RESOLUTION_SOURCE_CWD
                return workspace

        active_workspace_root = self.active_workspace_root
        if active_workspace_root is not None:
            workspace = WorkspacePaths.from_root(active_workspace_root)
            if workspace.has_source_checkout() or workspace.has_workspace_config():
                self._workspace_resolution_source = WORKSPACE_RESOLUTION_SOURCE_ACTIVE
                return workspace
            raise ProjectRootResolutionError(
                "The remembered PortWorld workspace is no longer valid. "
                "Rerun `portworld init` to refresh the default operator workspace, "
                "or pass --project-root to point at a valid repo or workspace."
            )

        raise ProjectRootResolutionError(
            "Could not find a PortWorld workspace from the current directory. "
            "Expected either a source checkout or .portworld/project.json. "
            "Use --project-root to point at the workspace root, or run `portworld init` "
            "to create the default operator workspace."
        )

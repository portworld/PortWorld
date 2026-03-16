from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portworld_cli.workspace.machine_state import load_machine_state
from portworld_cli.workspace.paths import ProjectRootResolutionError, WorkspacePaths


WORKSPACE_RESOLUTION_SOURCE_EXPLICIT = "explicit"
WORKSPACE_RESOLUTION_SOURCE_CWD = "cwd"
WORKSPACE_RESOLUTION_SOURCE_ACTIVE = "active_workspace"


@dataclass(frozen=True, slots=True)
class ResolvedWorkspace:
    workspace_paths: WorkspacePaths
    workspace_resolution_source: str
    active_workspace_root: Path | None


def resolve_workspace(
    *,
    explicit_root: Path | None,
    start: Path | None = None,
) -> ResolvedWorkspace:
    active_workspace_root = load_machine_state().active_workspace_root
    if explicit_root is not None:
        workspace = WorkspacePaths.from_root(explicit_root)
        if workspace.has_source_checkout() or workspace.has_workspace_config():
            return ResolvedWorkspace(
                workspace_paths=workspace,
                workspace_resolution_source=WORKSPACE_RESOLUTION_SOURCE_EXPLICIT,
                active_workspace_root=active_workspace_root,
            )
        raise ProjectRootResolutionError(
            f"{workspace.workspace_root} is not a valid PortWorld workspace. "
            "Expected either a source checkout or .portworld/project.json."
        )

    current = (start or Path.cwd()).resolve()
    candidates = (current,) + tuple(current.parents)
    for candidate in candidates:
        workspace = WorkspacePaths.from_root(candidate)
        if workspace.has_source_checkout() or workspace.has_workspace_config():
            return ResolvedWorkspace(
                workspace_paths=workspace,
                workspace_resolution_source=WORKSPACE_RESOLUTION_SOURCE_CWD,
                active_workspace_root=active_workspace_root,
            )

    if active_workspace_root is not None:
        workspace = WorkspacePaths.from_root(active_workspace_root)
        if workspace.has_source_checkout() or workspace.has_workspace_config():
            return ResolvedWorkspace(
                workspace_paths=workspace,
                workspace_resolution_source=WORKSPACE_RESOLUTION_SOURCE_ACTIVE,
                active_workspace_root=active_workspace_root,
            )
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

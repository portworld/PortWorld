from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portworld_cli.workspace.state.state_store import read_json_state, write_json_state


MACHINE_STATE_SCHEMA_VERSION = 1
MACHINE_STATE_FILE = Path.home() / ".portworld" / "machine-state.json"


@dataclass(frozen=True, slots=True)
class MachineState:
    schema_version: int
    active_workspace_root: Path | None

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "active_workspace_root": (
                None if self.active_workspace_root is None else str(self.active_workspace_root)
            ),
        }


def load_machine_state(path: Path = MACHINE_STATE_FILE) -> MachineState:
    payload = read_json_state(path)
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, int):
        schema_version = MACHINE_STATE_SCHEMA_VERSION

    active_workspace_root = payload.get("active_workspace_root")
    if isinstance(active_workspace_root, str) and active_workspace_root.strip():
        root = Path(active_workspace_root).expanduser()
    else:
        root = None

    return MachineState(
        schema_version=schema_version,
        active_workspace_root=None if root is None else root.resolve(),
    )


def write_machine_state(
    state: MachineState,
    path: Path = MACHINE_STATE_FILE,
) -> None:
    write_json_state(path, state.to_payload())


def remember_active_workspace(
    workspace_root: Path,
    path: Path = MACHINE_STATE_FILE,
) -> MachineState:
    state = MachineState(
        schema_version=MACHINE_STATE_SCHEMA_VERSION,
        active_workspace_root=workspace_root.expanduser().resolve(),
    )
    write_machine_state(state, path=path)
    return state

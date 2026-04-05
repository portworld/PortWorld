from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(slots=True)
class CLIContext:
    project_root_override: Path | None
    verbose: bool
    json_output: bool
    non_interactive: bool
    yes: bool

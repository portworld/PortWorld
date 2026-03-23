from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Mapping

from portworld_cli.extensions.manifest import ExtensionManifestError, load_manifest
from portworld_cli.extensions.types import (
    MCP_LAUNCHER_NPM_EXEC,
    MCP_LAUNCHER_NPX,
    MCP_TRANSPORT_STDIO,
)


@dataclass(frozen=True, slots=True)
class NodeLauncherReadiness:
    enabled_count: int
    launchers: tuple[str, ...]
    missing_binaries: tuple[str, ...]
    source: str = "host"
    error: str | None = None

    @property
    def bootstrap_required(self) -> bool:
        return self.enabled_count > 0 and bool(self.missing_binaries)

    def to_payload(self) -> dict[str, object]:
        return {
            "enabled_count": self.enabled_count,
            "launchers": list(self.launchers),
            "missing_binaries": list(self.missing_binaries),
            "source": self.source,
            "bootstrap_required": self.bootstrap_required,
            "error": self.error,
        }


def collect_node_launcher_readiness(manifest_path: Path) -> NodeLauncherReadiness:
    try:
        manifest = load_manifest(manifest_path)
    except ExtensionManifestError as exc:
        return NodeLauncherReadiness(
            enabled_count=0,
            launchers=(),
            missing_binaries=(),
            source="host",
            error=str(exc),
        )

    required_binaries: list[str] = []
    launchers: set[str] = set()
    enabled_count = 0

    for entry in manifest.installed:
        if not entry.enabled or entry.mcp_server is None:
            continue
        if entry.mcp_server.transport.strip() != MCP_TRANSPORT_STDIO:
            continue
        launcher = entry.mcp_server.launcher.strip()
        if launcher not in {MCP_LAUNCHER_NPX, MCP_LAUNCHER_NPM_EXEC}:
            continue
        enabled_count += 1
        launchers.add(launcher)
        required_binaries.extend(("node", "npm"))
        if launcher == MCP_LAUNCHER_NPX:
            required_binaries.append("npx")

    missing = sorted({name for name in required_binaries if shutil.which(name) is None})
    return NodeLauncherReadiness(
        enabled_count=enabled_count,
        launchers=tuple(sorted(launchers)),
        missing_binaries=tuple(missing),
        source="host",
    )


def collect_backend_node_launcher_readiness(
    extension_health: Mapping[str, object] | None,
) -> NodeLauncherReadiness:
    if not isinstance(extension_health, Mapping):
        return NodeLauncherReadiness(
            enabled_count=0,
            launchers=(),
            missing_binaries=(),
            source="backend",
            error="extension_health is unavailable",
        )

    runtime_prerequisites = extension_health.get("runtime_prerequisites")
    if not isinstance(runtime_prerequisites, Mapping):
        return NodeLauncherReadiness(
            enabled_count=0,
            launchers=(),
            missing_binaries=(),
            source="backend",
            error="extension_health.runtime_prerequisites is unavailable",
        )

    enabled_count = _read_int(runtime_prerequisites.get("node_launcher_enabled_count"))
    missing_binaries = tuple(
        sorted(
            item.strip()
            for item in _read_string_list(runtime_prerequisites.get("missing_binaries"))
            if item.strip()
        )
    )
    required_binaries = set(_read_string_list(runtime_prerequisites.get("required_binaries")))
    launchers: list[str] = []
    if "npx" in required_binaries:
        launchers.append(MCP_LAUNCHER_NPX)
    if {"node", "npm"}.issubset(required_binaries):
        launchers.append(MCP_LAUNCHER_NPM_EXEC)

    return NodeLauncherReadiness(
        enabled_count=enabled_count,
        launchers=tuple(sorted(set(launchers))),
        missing_binaries=missing_binaries,
        source="backend",
    )


def _read_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _read_string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))

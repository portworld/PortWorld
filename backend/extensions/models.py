from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ExtensionKind = Literal["tool_package", "mcp_server"]
MCPTransport = Literal["stdio", "streamable_http"]
MCPLauncher = Literal["direct", "npx", "npm_exec"]


@dataclass(frozen=True, slots=True)
class ToolPackageSpec:
    package_ref: str = ""
    entry_point: str | None = None
    install_strategy: str = "uv_pip_target"


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    transport: MCPTransport
    launcher: MCPLauncher = "direct"
    command: str | None = None
    package: str | None = None
    package_version: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env_bindings: tuple[str, ...] = ()
    headers_from_env: tuple[str, ...] = ()
    cwd: str | None = None
    namespace_prefix: str | None = None
    startup_timeout_seconds: float = 8.0


@dataclass(frozen=True, slots=True)
class InstalledExtension:
    id: str
    kind: ExtensionKind
    source: str = "catalog"
    enabled: bool = True
    package: str | None = None
    summary: str | None = None
    install_metadata: dict[str, str] = field(default_factory=dict)
    required_env_keys: tuple[str, ...] = ()
    tool_package: ToolPackageSpec | None = None
    mcp_server: MCPServerSpec | None = None


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    schema_version: int
    source_path: Path
    extensions: tuple[InstalledExtension, ...]
    local_definitions: tuple[InstalledExtension, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtensionHealthRecord:
    extension_id: str
    kind: ExtensionKind
    enabled: bool
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtensionRuntimePrerequisites:
    node_launcher_enabled_count: int = 0
    required_binaries: tuple[str, ...] = ()
    missing_binaries: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_binaries

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_launcher_enabled_count": self.node_launcher_enabled_count,
            "required_binaries": list(self.required_binaries),
            "missing_binaries": list(self.missing_binaries),
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class ExtensionHealthSummary:
    manifest_path: str | None
    configured: bool
    loaded: int
    enabled: int
    active: int
    failed: int
    records: tuple[ExtensionHealthRecord, ...] = ()
    runtime_prerequisites: ExtensionRuntimePrerequisites = field(
        default_factory=ExtensionRuntimePrerequisites
    )

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "configured": self.configured,
            "loaded": self.loaded,
            "enabled": self.enabled,
            "active": self.active,
            "failed": self.failed,
            "ok": self.ok,
            "runtime_prerequisites": self.runtime_prerequisites.to_dict(),
            "records": [
                {
                    "extension_id": record.extension_id,
                    "kind": record.kind,
                    "enabled": record.enabled,
                    "ok": record.ok,
                    "message": record.message,
                    "details": dict(record.details),
                }
                for record in self.records
            ],
        }

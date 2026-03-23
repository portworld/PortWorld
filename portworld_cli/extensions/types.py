from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


EXTENSIONS_SCHEMA_VERSION = 1
EXTENSION_SOURCE_CATALOG = "catalog"
EXTENSION_SOURCE_LOCAL = "local"
EXTENSION_KIND_TOOL_PACKAGE = "tool_package"
EXTENSION_KIND_MCP_SERVER = "mcp_server"
MCP_TRANSPORT_STDIO = "stdio"
MCP_TRANSPORT_STREAMABLE_HTTP = "streamable_http"
MCP_LAUNCHER_DIRECT = "direct"
MCP_LAUNCHER_NPX = "npx"
MCP_LAUNCHER_NPM_EXEC = "npm_exec"

ExtensionSource = Literal["catalog", "local"]
ExtensionKind = Literal["tool_package", "mcp_server"]


@dataclass(frozen=True, slots=True)
class ToolPackageSpec:
    package_ref: str
    entry_point: str | None = None
    install_strategy: str = "uv_pip_target"

    def to_payload(self) -> dict[str, Any]:
        return {
            "package_ref": self.package_ref,
            "entry_point": self.entry_point,
            "install_strategy": self.install_strategy,
        }


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    transport: str
    launcher: str = MCP_LAUNCHER_DIRECT
    command: str | None = None
    package: str | None = None
    package_version: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    env_bindings: tuple[str, ...] = ()
    headers_from_env: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "launcher": self.launcher,
            "command": self.command,
            "package": self.package,
            "package_version": self.package_version,
            "args": list(self.args),
            "url": self.url,
            "env_bindings": list(self.env_bindings),
            "headers_from_env": list(self.headers_from_env),
        }


@dataclass(frozen=True, slots=True)
class LocalExtensionDefinition:
    id: str
    kind: ExtensionKind
    summary: str
    required_env_keys: tuple[str, ...] = ()
    tool_package: ToolPackageSpec | None = None
    mcp_server: MCPServerSpec | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "required_env_keys": list(self.required_env_keys),
            "tool_package": None if self.tool_package is None else self.tool_package.to_payload(),
            "mcp_server": None if self.mcp_server is None else self.mcp_server.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class InstalledExtension:
    id: str
    kind: ExtensionKind
    source: ExtensionSource
    enabled: bool = True
    required_env_keys: tuple[str, ...] = ()
    tool_package: ToolPackageSpec | None = None
    mcp_server: MCPServerSpec | None = None
    summary: str | None = None
    install_metadata: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "enabled": self.enabled,
            "required_env_keys": list(self.required_env_keys),
            "tool_package": None if self.tool_package is None else self.tool_package.to_payload(),
            "mcp_server": None if self.mcp_server is None else self.mcp_server.to_payload(),
            "summary": self.summary,
            "install_metadata": dict(self.install_metadata),
        }


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    schema_version: int = EXTENSIONS_SCHEMA_VERSION
    installed: tuple[InstalledExtension, ...] = ()
    local_definitions: tuple[LocalExtensionDefinition, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "installed": [entry.to_payload() for entry in self.installed],
            "local_definitions": [entry.to_payload() for entry in self.local_definitions],
        }


@dataclass(frozen=True, slots=True)
class ExtensionCatalogEntry:
    id: str
    kind: ExtensionKind
    summary: str
    required_env_keys: tuple[str, ...] = ()
    tool_package: ToolPackageSpec | None = None
    mcp_server: MCPServerSpec | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "required_env_keys": list(self.required_env_keys),
            "tool_package": None if self.tool_package is None else self.tool_package.to_payload(),
            "mcp_server": None if self.mcp_server is None else self.mcp_server.to_payload(),
        }

from __future__ import annotations

from portworld_cli.extensions.types import (
    EXTENSION_KIND_MCP_SERVER,
    EXTENSION_KIND_TOOL_PACKAGE,
    MCP_LAUNCHER_NPX,
    MCP_TRANSPORT_STDIO,
    ExtensionCatalogEntry,
    MCPServerSpec,
    ToolPackageSpec,
)


OFFICIAL_EXTENSION_CATALOG: tuple[ExtensionCatalogEntry, ...] = (
    ExtensionCatalogEntry(
        id="mcp-filesystem-node",
        kind=EXTENSION_KIND_MCP_SERVER,
        summary=(
            "Node MCP filesystem server launched over stdio using npx "
            "(host prerequisites via install.sh, container prerequisites via the backend image)."
        ),
        required_env_keys=(),
        mcp_server=MCPServerSpec(
            transport=MCP_TRANSPORT_STDIO,
            launcher=MCP_LAUNCHER_NPX,
            package="@modelcontextprotocol/server-filesystem",
            package_version="2026.1.14",
            args=(".",),
        ),
    ),
    ExtensionCatalogEntry(
        id="mcp-sequential-thinking-node",
        kind=EXTENSION_KIND_MCP_SERVER,
        summary=(
            "Node MCP sequential-thinking server launched over stdio using npx "
            "(host prerequisites via install.sh, container prerequisites via the backend image)."
        ),
        required_env_keys=(),
        mcp_server=MCPServerSpec(
            transport=MCP_TRANSPORT_STDIO,
            launcher=MCP_LAUNCHER_NPX,
            package="@modelcontextprotocol/server-sequential-thinking",
            package_version="2025.12.18",
        ),
    ),
    ExtensionCatalogEntry(
        id="mcp-memory-node",
        kind=EXTENSION_KIND_MCP_SERVER,
        summary=(
            "Node MCP knowledge-graph memory server launched over stdio using npx "
            "(host prerequisites via install.sh, container prerequisites via the backend image)."
        ),
        required_env_keys=(),
        mcp_server=MCPServerSpec(
            transport=MCP_TRANSPORT_STDIO,
            launcher=MCP_LAUNCHER_NPX,
            package="@modelcontextprotocol/server-memory",
            package_version="2026.1.26",
        ),
    ),
    ExtensionCatalogEntry(
        id="mcp-fetch-http",
        kind=EXTENSION_KIND_MCP_SERVER,
        summary="MCP server over streamable HTTP for fetch/web context tools.",
        required_env_keys=(),
        mcp_server=MCPServerSpec(
            transport="streamable_http",
            url="http://127.0.0.1:8000/mcp",
        ),
    ),
    ExtensionCatalogEntry(
        id="tooling-weather-example",
        kind=EXTENSION_KIND_TOOL_PACKAGE,
        summary="Example Python tool package extension loaded via entry points.",
        required_env_keys=(),
        tool_package=ToolPackageSpec(
            package_ref="portworld-tooling-weather-example",
        ),
    ),
)


def list_official_extensions() -> tuple[ExtensionCatalogEntry, ...]:
    return OFFICIAL_EXTENSION_CATALOG


def resolve_official_extension(extension_id: str) -> ExtensionCatalogEntry | None:
    normalized = extension_id.strip().lower()
    for entry in OFFICIAL_EXTENSION_CATALOG:
        if entry.id == normalized:
            return entry
    return None

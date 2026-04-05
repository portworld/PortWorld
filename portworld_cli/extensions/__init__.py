from __future__ import annotations

from portworld_cli.extensions.catalog import list_official_extensions, resolve_official_extension
from portworld_cli.extensions.doctor import ExtensionDoctorResult, run_extension_doctor
from portworld_cli.extensions.install import (
    ExtensionInstallError,
    InstallSummary,
    reconcile_python_extension_install_dir,
)
from portworld_cli.extensions.manifest import (
    ExtensionManifestError,
    ensure_manifest_file,
    get_local_definition,
    load_local_definition_file,
    load_manifest,
    remove_installed_extension,
    set_installed_enabled,
    upsert_installed_extension,
    upsert_local_definition,
    write_manifest,
)
from portworld_cli.extensions.node_launchers import (
    NodeLauncherReadiness,
    collect_backend_node_launcher_readiness,
    collect_node_launcher_readiness,
)
from portworld_cli.extensions.runtime_env import (
    EXTENSIONS_MANIFEST_ENV_KEY,
    EXTENSIONS_PYTHON_PATH_ENV_KEY,
    build_extension_runtime_env_overrides,
)
from portworld_cli.extensions.summary import ExtensionsSummary, collect_extensions_summary
from portworld_cli.extensions.types import (
    EXTENSION_KIND_MCP_SERVER,
    EXTENSION_KIND_TOOL_PACKAGE,
    EXTENSION_SOURCE_CATALOG,
    EXTENSION_SOURCE_LOCAL,
    ExtensionCatalogEntry,
    ExtensionManifest,
    InstalledExtension,
    LocalExtensionDefinition,
    MCPServerSpec,
    ToolPackageSpec,
)

__all__ = [
    "EXTENSIONS_MANIFEST_ENV_KEY",
    "EXTENSIONS_PYTHON_PATH_ENV_KEY",
    "EXTENSION_KIND_MCP_SERVER",
    "EXTENSION_KIND_TOOL_PACKAGE",
    "EXTENSION_SOURCE_CATALOG",
    "EXTENSION_SOURCE_LOCAL",
    "ExtensionCatalogEntry",
    "ExtensionDoctorResult",
    "ExtensionInstallError",
    "ExtensionManifest",
    "ExtensionManifestError",
    "InstallSummary",
    "InstalledExtension",
    "ExtensionsSummary",
    "LocalExtensionDefinition",
    "MCPServerSpec",
    "NodeLauncherReadiness",
    "ToolPackageSpec",
    "build_extension_runtime_env_overrides",
    "collect_backend_node_launcher_readiness",
    "collect_extensions_summary",
    "ensure_manifest_file",
    "get_local_definition",
    "list_official_extensions",
    "load_local_definition_file",
    "load_manifest",
    "collect_node_launcher_readiness",
    "reconcile_python_extension_install_dir",
    "remove_installed_extension",
    "resolve_official_extension",
    "run_extension_doctor",
    "set_installed_enabled",
    "upsert_installed_extension",
    "upsert_local_definition",
    "write_manifest",
]

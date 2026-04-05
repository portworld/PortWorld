from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from portworld_cli.extensions.types import (
    EXTENSION_KIND_MCP_SERVER,
    EXTENSION_KIND_TOOL_PACKAGE,
    MCP_LAUNCHER_DIRECT,
    MCP_LAUNCHER_NPM_EXEC,
    MCP_LAUNCHER_NPX,
    MCP_TRANSPORT_STDIO,
    MCP_TRANSPORT_STREAMABLE_HTTP,
    EXTENSION_SOURCE_CATALOG,
    EXTENSION_SOURCE_LOCAL,
    EXTENSIONS_SCHEMA_VERSION,
    ExtensionManifest,
    InstalledExtension,
    LocalExtensionDefinition,
    MCPServerSpec,
    ToolPackageSpec,
)


class ExtensionManifestError(RuntimeError):
    """Raised when `.portworld/extensions.json` cannot be parsed or validated."""


def load_manifest(path: Path) -> ExtensionManifest:
    if not path.exists():
        return ExtensionManifest()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExtensionManifestError(f"Failed to parse extension manifest: {path}") from exc

    if not isinstance(payload, dict):
        raise ExtensionManifestError("Extension manifest must be a JSON object.")

    schema_version = _read_int(payload, "schema_version", default=EXTENSIONS_SCHEMA_VERSION)
    if schema_version != EXTENSIONS_SCHEMA_VERSION:
        raise ExtensionManifestError(
            f"Unsupported extension manifest schema_version={schema_version}."
        )

    installed_payload = payload.get("installed", [])
    if not isinstance(installed_payload, list):
        raise ExtensionManifestError("Extension manifest field `installed` must be an array.")

    local_payload = payload.get("local_definitions", [])
    if not isinstance(local_payload, list):
        raise ExtensionManifestError("Extension manifest field `local_definitions` must be an array.")

    installed = tuple(_parse_installed_extension(entry) for entry in installed_payload)
    local_definitions = tuple(_parse_local_definition(entry) for entry in local_payload)
    _ensure_unique_ids(installed, local_definitions)
    return ExtensionManifest(
        schema_version=schema_version,
        installed=installed,
        local_definitions=local_definitions,
    )


def write_manifest(path: Path, manifest: ExtensionManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest.to_payload(), handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def ensure_manifest_file(path: Path) -> ExtensionManifest:
    manifest = load_manifest(path)
    if not path.exists():
        write_manifest(path, manifest)
    return manifest


def upsert_installed_extension(
    manifest: ExtensionManifest,
    extension: InstalledExtension,
) -> ExtensionManifest:
    next_entries = [entry for entry in manifest.installed if entry.id != extension.id]
    next_entries.append(extension)
    return replace(manifest, installed=tuple(sorted(next_entries, key=lambda item: item.id)))


def remove_installed_extension(
    manifest: ExtensionManifest,
    extension_id: str,
) -> ExtensionManifest:
    normalized = extension_id.strip().lower()
    next_entries = tuple(entry for entry in manifest.installed if entry.id != normalized)
    next_local_definitions = tuple(
        entry for entry in manifest.local_definitions if entry.id != normalized
    )
    return replace(
        manifest,
        installed=next_entries,
        local_definitions=next_local_definitions,
    )


def set_installed_enabled(
    manifest: ExtensionManifest,
    *,
    extension_id: str,
    enabled: bool,
) -> ExtensionManifest:
    normalized = extension_id.strip().lower()
    found = False
    next_entries: list[InstalledExtension] = []
    for entry in manifest.installed:
        if entry.id != normalized:
            next_entries.append(entry)
            continue
        found = True
        next_entries.append(replace(entry, enabled=enabled))
    if not found:
        raise ExtensionManifestError(f"Extension is not installed: {extension_id}")
    return replace(manifest, installed=tuple(next_entries))


def upsert_local_definition(
    manifest: ExtensionManifest,
    definition: LocalExtensionDefinition,
) -> ExtensionManifest:
    next_entries = [entry for entry in manifest.local_definitions if entry.id != definition.id]
    next_entries.append(definition)
    return replace(
        manifest,
        local_definitions=tuple(sorted(next_entries, key=lambda item: item.id)),
    )


def get_local_definition(
    manifest: ExtensionManifest,
    extension_id: str,
) -> LocalExtensionDefinition | None:
    normalized = extension_id.strip().lower()
    for entry in manifest.local_definitions:
        if entry.id == normalized:
            return entry
    return None


def load_local_definition_file(path: Path) -> LocalExtensionDefinition:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExtensionManifestError(f"Invalid extension definition file: {path}") from exc
    if not isinstance(payload, dict):
        raise ExtensionManifestError(f"Extension definition file must contain an object: {path}")
    return _parse_local_definition(payload)


def _parse_installed_extension(payload: Any) -> InstalledExtension:
    if not isinstance(payload, dict):
        raise ExtensionManifestError("Each installed extension entry must be an object.")

    kind = _read_extension_kind(payload, "kind")
    source = _read_source(payload, "source")
    return InstalledExtension(
        id=_read_id(payload, "id"),
        kind=kind,
        source=source,
        enabled=_read_bool(payload, "enabled", default=True),
        required_env_keys=_read_string_list(payload, "required_env_keys"),
        tool_package=_parse_tool_package(payload.get("tool_package"), required=kind == EXTENSION_KIND_TOOL_PACKAGE),
        mcp_server=_parse_mcp_server(payload.get("mcp_server"), required=kind == EXTENSION_KIND_MCP_SERVER),
        summary=_read_optional_string(payload, "summary"),
        install_metadata=_read_string_dict(payload, "install_metadata"),
    )


def _parse_local_definition(payload: Any) -> LocalExtensionDefinition:
    if not isinstance(payload, dict):
        raise ExtensionManifestError("Each local extension definition entry must be an object.")
    kind = _read_extension_kind(payload, "kind")
    return LocalExtensionDefinition(
        id=_read_id(payload, "id"),
        kind=kind,
        summary=_read_string(payload, "summary", default=""),
        required_env_keys=_read_string_list(payload, "required_env_keys"),
        tool_package=_parse_tool_package(payload.get("tool_package"), required=kind == EXTENSION_KIND_TOOL_PACKAGE),
        mcp_server=_parse_mcp_server(payload.get("mcp_server"), required=kind == EXTENSION_KIND_MCP_SERVER),
    )


def _parse_tool_package(payload: Any, *, required: bool) -> ToolPackageSpec | None:
    if payload is None:
        if required:
            raise ExtensionManifestError("tool_package is required for tool_package extensions.")
        return None
    if not isinstance(payload, dict):
        raise ExtensionManifestError("tool_package must be an object.")
    return ToolPackageSpec(
        package_ref=_read_string(payload, "package_ref", default=""),
        entry_point=_read_optional_string(payload, "entry_point"),
        install_strategy=_read_string(payload, "install_strategy", default="uv_pip_target"),
    )


def _parse_mcp_server(payload: Any, *, required: bool) -> MCPServerSpec | None:
    if payload is None:
        if required:
            raise ExtensionManifestError("mcp_server is required for mcp_server extensions.")
        return None
    if not isinstance(payload, dict):
        raise ExtensionManifestError("mcp_server must be an object.")
    transport = _read_string(payload, "transport", default="")
    if transport not in {MCP_TRANSPORT_STDIO, MCP_TRANSPORT_STREAMABLE_HTTP}:
        raise ExtensionManifestError(
            "mcp_server.transport must be one of: stdio, streamable_http."
        )

    launcher = _read_string(payload, "launcher", default=MCP_LAUNCHER_DIRECT)
    if launcher not in {MCP_LAUNCHER_DIRECT, MCP_LAUNCHER_NPX, MCP_LAUNCHER_NPM_EXEC}:
        raise ExtensionManifestError(
            "mcp_server.launcher must be one of: direct, npx, npm_exec."
        )

    command = _read_optional_string(payload, "command")
    package = _read_optional_string(payload, "package")
    package_version = _read_optional_string(payload, "package_version")
    url = _read_optional_string(payload, "url")

    if transport == MCP_TRANSPORT_STDIO:
        if launcher == MCP_LAUNCHER_DIRECT and not command:
            raise ExtensionManifestError(
                "mcp_server.command is required when transport=stdio and launcher=direct."
            )
        if launcher in {MCP_LAUNCHER_NPX, MCP_LAUNCHER_NPM_EXEC} and not package:
            raise ExtensionManifestError(
                "mcp_server.package is required when transport=stdio and launcher is npx or npm_exec."
            )
    if transport == MCP_TRANSPORT_STREAMABLE_HTTP and not url:
        raise ExtensionManifestError(
            "mcp_server.url is required when transport=streamable_http."
        )

    return MCPServerSpec(
        transport=transport,
        launcher=launcher,
        command=command,
        package=package,
        package_version=package_version,
        args=_read_string_list(payload, "args"),
        url=url,
        env_bindings=_read_string_list(payload, "env_bindings"),
        headers_from_env=_read_string_list(payload, "headers_from_env"),
    )


def _ensure_unique_ids(
    installed: Iterable[InstalledExtension],
    local_definitions: Iterable[LocalExtensionDefinition],
) -> None:
    seen: set[str] = set()
    for entry in installed:
        if entry.id in seen:
            raise ExtensionManifestError(f"Duplicate extension id in installed: {entry.id}")
        seen.add(entry.id)

    seen_local: set[str] = set()
    for entry in local_definitions:
        if entry.id in seen_local:
            raise ExtensionManifestError(f"Duplicate extension id in local_definitions: {entry.id}")
        seen_local.add(entry.id)


def _read_id(payload: Mapping[str, Any], key: str) -> str:
    value = _read_string(payload, key, default="")
    normalized = value.strip().lower()
    if not normalized:
        raise ExtensionManifestError("Extension id must be a non-empty string.")
    return normalized


def _read_extension_kind(payload: Mapping[str, Any], key: str) -> str:
    value = _read_string(payload, key, default="")
    if value not in {EXTENSION_KIND_TOOL_PACKAGE, EXTENSION_KIND_MCP_SERVER}:
        raise ExtensionManifestError(
            f"Unsupported extension kind: {value!r}. Expected tool_package or mcp_server."
        )
    return value


def _read_source(payload: Mapping[str, Any], key: str) -> str:
    value = _read_string(payload, key, default="")
    if value not in {EXTENSION_SOURCE_CATALOG, EXTENSION_SOURCE_LOCAL}:
        raise ExtensionManifestError(
            f"Unsupported extension source: {value!r}. Expected catalog or local."
        )
    return value


def _read_string(payload: Mapping[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ExtensionManifestError(f"Extension manifest field `{key}` must be a string.")
    return value.strip() or default


def _read_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtensionManifestError(f"Extension manifest field `{key}` must be a string or null.")
    normalized = value.strip()
    return normalized or None


def _read_bool(payload: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ExtensionManifestError(f"Extension manifest field `{key}` must be a boolean.")
    return value


def _read_int(payload: Mapping[str, Any], key: str, *, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int):
        raise ExtensionManifestError(f"Extension manifest field `{key}` must be an integer.")
    return value


def _read_string_list(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionManifestError(f"Extension manifest field `{key}` must be an array.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ExtensionManifestError(f"Extension manifest field `{key}` must contain only strings.")
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return tuple(result)


def _read_string_dict(payload: Mapping[str, Any], key: str) -> dict[str, str]:
    value = payload.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExtensionManifestError(f"Extension manifest field `{key}` must be an object.")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ExtensionManifestError(
                f"Extension manifest field `{key}` must map string keys to string values."
            )
        key_value = raw_key.strip()
        if not key_value:
            continue
        result[key_value] = raw_value
    return result

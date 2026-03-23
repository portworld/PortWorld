from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.extensions.models import (
    ExtensionManifest,
    InstalledExtension,
    MCPServerSpec,
    ToolPackageSpec,
)


class ExtensionManifestError(RuntimeError):
    """Raised when the extension manifest is missing or invalid."""


def load_extension_manifest(path: Path) -> ExtensionManifest:
    if not path.exists():
        raise ExtensionManifestError(f"Extension manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExtensionManifestError(f"Failed to parse extension manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ExtensionManifestError("Extension manifest must be a JSON object.")

    schema_version = _read_int(payload, "schema_version", default=1)
    if schema_version != 1:
        raise ExtensionManifestError(
            f"Unsupported extension manifest schema_version={schema_version}. Supported: 1."
        )

    raw_extensions = payload.get("installed")
    if raw_extensions is None:
        raw_extensions = payload.get("extensions", [])
    if raw_extensions is None:
        raw_extensions = []
    if not isinstance(raw_extensions, list):
        raise ExtensionManifestError("installed/extensions must be a JSON array.")

    raw_local_definitions = payload.get("local_definitions", [])
    if raw_local_definitions is None:
        raw_local_definitions = []
    if not isinstance(raw_local_definitions, list):
        raise ExtensionManifestError("local_definitions must be a JSON array.")

    extensions: list[InstalledExtension] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_extensions):
        if not isinstance(raw, dict):
            raise ExtensionManifestError(f"extensions[{index}] must be a JSON object.")
        item = _parse_extension(raw, index=index)
        if item.id in seen_ids:
            raise ExtensionManifestError(f"Duplicate extension id: {item.id}")
        seen_ids.add(item.id)
        extensions.append(item)

    local_definitions: list[InstalledExtension] = []
    for index, raw in enumerate(raw_local_definitions):
        if not isinstance(raw, dict):
            raise ExtensionManifestError(f"local_definitions[{index}] must be a JSON object.")
        local_item = _parse_extension(raw, index=index)
        local_definitions.append(local_item)

    return ExtensionManifest(
        schema_version=schema_version,
        source_path=path,
        extensions=tuple(extensions),
        local_definitions=tuple(local_definitions),
    )


def _parse_extension(payload: dict[str, Any], *, index: int) -> InstalledExtension:
    extension_id = _read_string(payload, "id")
    kind = _read_string(payload, "kind")
    if kind not in {"tool_package", "mcp_server"}:
        raise ExtensionManifestError(
            f"extensions[{index}].kind must be one of: tool_package, mcp_server."
        )
    source = _read_optional_string(payload, "source") or "catalog"
    enabled = _read_bool(payload, "enabled", default=True)
    package = _read_optional_string(payload, "package")
    summary = _read_optional_string(payload, "summary")
    install_metadata = _read_string_map(payload, "install_metadata")
    required_env_keys = tuple(_read_string_list(payload, "required_env_keys"))

    tool_package = None
    if kind == "tool_package":
        spec = payload.get("tool_package", {})
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise ExtensionManifestError(
                f"extensions[{index}].tool_package must be a JSON object."
            )
        tool_package = ToolPackageSpec(
            package_ref=_read_string(spec, "package_ref", default=""),
            entry_point=(
                _read_optional_string(spec, "entry_point")
                or _read_optional_string(spec, "entrypoint")
            ),
            install_strategy=_read_string(spec, "install_strategy", default="uv_pip_target"),
        )

    mcp_server = None
    if kind == "mcp_server":
        spec = payload.get("mcp_server", {})
        if not isinstance(spec, dict):
            raise ExtensionManifestError(
                f"extensions[{index}].mcp_server must be a JSON object."
            )
        transport = _read_string(spec, "transport")
        if transport not in {"stdio", "streamable_http"}:
            raise ExtensionManifestError(
                f"extensions[{index}].mcp_server.transport must be stdio or streamable_http."
            )
        mcp_server = MCPServerSpec(
            transport=transport,
            launcher=_read_string(
                spec,
                "launcher",
                default=(
                    _read_optional_string(spec, "launch_strategy")
                    or _read_optional_string(spec, "launcher_type")
                    or "direct"
                ),
            ),
            command=_read_optional_string(spec, "command"),
            package=(
                _read_optional_string(spec, "package")
                or _read_optional_string(spec, "package_name")
                or _read_optional_string(spec, "npm_package")
            ),
            package_version=(
                _read_optional_string(spec, "package_version")
                or _read_optional_string(spec, "version")
            ),
            args=tuple(_read_string_list(spec, "args")),
            url=_read_optional_string(spec, "url"),
            env_bindings=tuple(_read_string_list(spec, "env_bindings")),
            headers_from_env=tuple(_read_string_list(spec, "headers_from_env")),
            cwd=_read_optional_string(spec, "cwd"),
            namespace_prefix=_read_optional_string(spec, "namespace_prefix"),
            startup_timeout_seconds=float(_read_number(spec, "startup_timeout_seconds", default=8)),
        )
        if mcp_server.launcher not in {"direct", "npx", "npm_exec"}:
            raise ExtensionManifestError(
                f"extensions[{index}].mcp_server.launcher must be direct, npx, or npm_exec."
            )
        if mcp_server.transport == "stdio" and not (mcp_server.command or "").strip():
            if mcp_server.launcher == "direct":
                raise ExtensionManifestError(
                    f"extensions[{index}] stdio MCP with launcher=direct requires mcp_server.command."
                )
        if (
            mcp_server.transport == "stdio"
            and mcp_server.launcher in {"npx", "npm_exec"}
            and not (mcp_server.package or "").strip()
        ):
            raise ExtensionManifestError(
                f"extensions[{index}] stdio MCP with launcher={mcp_server.launcher} requires mcp_server.package."
            )
        if mcp_server.transport == "streamable_http" and not (mcp_server.url or "").strip():
            raise ExtensionManifestError(
                f"extensions[{index}] streamable_http MCP requires mcp_server.url."
            )

    return InstalledExtension(
        id=extension_id,
        kind=kind,
        source=source,
        enabled=enabled,
        package=package,
        summary=summary,
        install_metadata=install_metadata,
        required_env_keys=required_env_keys,
        tool_package=tool_package,
        mcp_server=mcp_server,
    )


def _read_string(payload: dict[str, Any], key: str, *, default: str | None = None) -> str:
    value = payload.get(key)
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise ExtensionManifestError(f"{key} must be a string.")
    text = value.strip()
    if not text:
        if default is not None:
            return default
        raise ExtensionManifestError(f"{key} must not be empty.")
    return text


def _read_optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtensionManifestError(f"{key} must be a string or null.")
    text = value.strip()
    return text or None


def _read_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ExtensionManifestError(f"{key} must be a boolean.")
    return value


def _read_int(payload: dict[str, Any], key: str, *, default: int) -> int:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, int):
        raise ExtensionManifestError(f"{key} must be an integer.")
    return value


def _read_number(payload: dict[str, Any], key: str, *, default: int | float) -> int | float:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, (int, float)):
        raise ExtensionManifestError(f"{key} must be a number.")
    return value


def _read_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExtensionManifestError(f"{key} must be an array of strings.")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ExtensionManifestError(f"{key} must contain only strings.")
        candidate = item.strip()
        if candidate:
            values.append(candidate)
    return values


def _read_string_map(payload: dict[str, Any], key: str) -> dict[str, str]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExtensionManifestError(f"{key} must be an object map of strings.")
    resolved: dict[str, str] = {}
    for map_key, map_value in value.items():
        if not isinstance(map_key, str) or not isinstance(map_value, str):
            raise ExtensionManifestError(f"{key} must contain only string keys and values.")
        if map_key.strip() and map_value.strip():
            resolved[map_key.strip()] = map_value.strip()
    return resolved

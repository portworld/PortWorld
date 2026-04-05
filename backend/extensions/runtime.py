from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import importlib
from importlib.metadata import entry_points
import logging
from pathlib import Path
import shutil
import sys
from typing import Any, Callable, Iterable
import httpx

from backend.core.settings import Settings
from backend.extensions.manifest import ExtensionManifestError, load_extension_manifest
from backend.extensions.models import (
    ExtensionHealthRecord,
    ExtensionHealthSummary,
    ExtensionManifest,
    ExtensionRuntimePrerequisites,
    InstalledExtension,
    MCPServerSpec,
)
from backend.tools.contracts import ToolCall, ToolDefinition, ToolResult
from backend.tools.registry import RealtimeToolRegistry, ToolRegistryError

logger = logging.getLogger(__name__)
STREAMABLE_HTTP_CONNECT_ATTEMPTS = 2
STREAMABLE_HTTP_RETRY_DELAY_SECONDS = 0.5


ToolContributor = Callable[[RealtimeToolRegistry, Any], None]


@dataclass(frozen=True, slots=True)
class MCPStartupError(RuntimeError):
    phase: str
    reason: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True)
class _MCPServerHandle:
    extension_id: str
    endpoint_label: str
    session: Any
    exit_stack: AsyncExitStack
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def call_tool(self, *, name: str, arguments: dict[str, Any]) -> Any:
        async with self.lock:
            return await self.session.call_tool(name=name, arguments=arguments)

    async def close(self) -> None:
        await self.exit_stack.aclose()


@dataclass(slots=True)
class ExtensionRuntime:
    manifest_path: str | None
    configured: bool
    contributors: list[ToolContributor] = field(default_factory=list)
    mcp_extensions: list[tuple[InstalledExtension, MCPServerSpec]] = field(default_factory=list)
    records: list[ExtensionHealthRecord] = field(default_factory=list)
    mcp_handles: list[_MCPServerHandle] = field(default_factory=list)
    discovered_mcp_tools: list[str] = field(default_factory=list)
    runtime_prerequisites: ExtensionRuntimePrerequisites = field(
        default_factory=ExtensionRuntimePrerequisites
    )

    @classmethod
    def from_settings(
        cls,
        *,
        settings: Settings,
        context: Any,
    ) -> "ExtensionRuntime":
        manifest_path = settings.portworld_extensions_manifest
        if manifest_path is None:
            return cls(manifest_path=None, configured=False)

        runtime = cls(
            manifest_path=str(manifest_path),
            configured=True,
        )
        try:
            manifest = load_extension_manifest(manifest_path)
        except ExtensionManifestError as exc:
            runtime.records.append(
                ExtensionHealthRecord(
                    extension_id="manifest",
                    kind="tool_package",
                    enabled=True,
                    ok=False,
                    message=str(exc),
                )
            )
            return runtime

        runtime._enable_extension_python_path(settings.portworld_extensions_python_path)
        runtime.runtime_prerequisites = collect_runtime_prerequisites(manifest)
        runtime._collect_contributors(manifest=manifest, context=context)
        runtime._collect_mcp_extensions(manifest=manifest)
        return runtime

    def summary(self) -> ExtensionHealthSummary:
        enabled = sum(1 for record in self.records if record.enabled)
        active = sum(1 for record in self.records if record.enabled and record.ok)
        failed = sum(1 for record in self.records if record.enabled and not record.ok)
        return ExtensionHealthSummary(
            manifest_path=self.manifest_path,
            configured=self.configured,
            loaded=len(self.records),
            enabled=enabled,
            active=active,
            failed=failed,
            records=tuple(self.records),
            runtime_prerequisites=self.runtime_prerequisites,
        )

    async def startup(self, *, registry: RealtimeToolRegistry) -> None:
        for extension, spec in self.mcp_extensions:
            await self._startup_mcp_extension(
                extension=extension,
                spec=spec,
                registry=registry,
            )

    async def shutdown(self) -> None:
        for handle in reversed(self.mcp_handles):
            try:
                await handle.close()
            except Exception:
                logger.exception(
                    "Failed closing MCP server handle extension_id=%s endpoint=%s",
                    handle.extension_id,
                    handle.endpoint_label,
                )
        self.mcp_handles.clear()

    def _enable_extension_python_path(self, python_path: Path | None) -> None:
        if python_path is None:
            return
        path = str(python_path.resolve())
        if path not in sys.path:
            sys.path.insert(0, path)

    def _collect_contributors(
        self,
        *,
        manifest: ExtensionManifest,
        context: Any,
    ) -> None:
        _ = context
        for extension in manifest.extensions:
            if not extension.enabled:
                self.records.append(
                    ExtensionHealthRecord(
                        extension_id=extension.id,
                        kind=extension.kind,
                        enabled=False,
                        ok=True,
                        message="disabled",
                    )
                )
                continue
            missing_env_keys = tuple(
                key for key in extension.required_env_keys if not (settings_value(key) or "").strip()
            )
            if missing_env_keys:
                self.records.append(
                    ExtensionHealthRecord(
                        extension_id=extension.id,
                        kind=extension.kind,
                        enabled=True,
                        ok=False,
                        message="Missing required environment keys.",
                        details={"missing_env_keys": list(missing_env_keys)},
                    )
                )
                continue
            if extension.kind != "tool_package":
                continue
            try:
                contributor = resolve_tool_contributor(extension)
                self.contributors.append(contributor)
                self.records.append(
                    ExtensionHealthRecord(
                        extension_id=extension.id,
                        kind=extension.kind,
                        enabled=True,
                        ok=True,
                        message="tool contributor loaded",
                    )
                )
            except Exception as exc:
                self.records.append(
                    ExtensionHealthRecord(
                        extension_id=extension.id,
                        kind=extension.kind,
                        enabled=True,
                        ok=False,
                        message=f"Failed loading tool contributor: {exc}",
                    )
                )

    def _collect_mcp_extensions(self, *, manifest: ExtensionManifest) -> None:
        for extension in manifest.extensions:
            if extension.kind != "mcp_server":
                continue
            if not extension.enabled:
                continue
            if extension.mcp_server is None:
                self.records.append(
                    ExtensionHealthRecord(
                        extension_id=extension.id,
                        kind=extension.kind,
                        enabled=True,
                        ok=False,
                        message="mcp_server extension missing mcp_server spec.",
                    )
                )
                continue
            missing_binaries = runtime_missing_binaries_for_spec(extension.mcp_server)
            if missing_binaries:
                self.records.append(
                    ExtensionHealthRecord(
                        extension_id=extension.id,
                        kind=extension.kind,
                        enabled=True,
                        ok=False,
                        message="Missing MCP runtime prerequisites on backend PATH.",
                        details={
                            "transport": extension.mcp_server.transport,
                            "launcher": extension.mcp_server.launcher,
                            "missing_binaries": list(missing_binaries),
                        },
                    )
                )
                continue
            self.mcp_extensions.append((extension, extension.mcp_server))

    async def _startup_mcp_extension(
        self,
        *,
        extension: InstalledExtension,
        spec: MCPServerSpec,
        registry: RealtimeToolRegistry,
    ) -> None:
        handle: _MCPServerHandle | None = None
        try:
            handle = await open_mcp_handle(extension_id=extension.id, spec=spec)
            tools = await list_mcp_tools(handle=handle, spec=spec)
            if not tools.tools:
                raise MCPStartupError(
                    phase="tool_discovery",
                    reason="no_tools",
                    message="MCP tool discovery returned no tools.",
                    details={
                        "transport": spec.transport,
                        "launcher": spec.launcher,
                        "endpoint_label": handle.endpoint_label,
                    },
                )
            for tool in tools.tools:
                tool_name = str(tool.name).strip()
                namespaced = (
                    f"{spec.namespace_prefix}__{tool_name}"
                    if (spec.namespace_prefix or "").strip()
                    else tool_name
                )
                registry.register(
                    definition=ToolDefinition(
                        name=namespaced,
                        description=(tool.description or "MCP-discovered tool."),
                        input_schema=dict(tool.inputSchema or {"type": "object"}),
                    ),
                    executor=MCPToolExecutor(
                        handle=handle,
                        extension_id=extension.id,
                        source_tool_name=tool_name,
                    ),
                )
                self.discovered_mcp_tools.append(namespaced)
            self.mcp_handles.append(handle)
            self.records.append(
                ExtensionHealthRecord(
                    extension_id=extension.id,
                    kind=extension.kind,
                    enabled=True,
                    ok=True,
                    message="MCP server connected and tools registered.",
                    details={
                        "tool_count": len(tools.tools),
                        "transport": spec.transport,
                        "launcher": spec.launcher,
                    },
                )
            )
        except ToolRegistryError as exc:
            self.records.append(
                ExtensionHealthRecord(
                    extension_id=extension.id,
                    kind=extension.kind,
                    enabled=True,
                    ok=False,
                    message=f"MCP tool registration failed: {exc}",
                )
            )
        except MCPStartupError as exc:
            self.records.append(
                ExtensionHealthRecord(
                    extension_id=extension.id,
                    kind=extension.kind,
                    enabled=True,
                    ok=False,
                    message=f"MCP {exc.phase} failed ({exc.reason}): {exc.message}",
                    details=dict(exc.details),
                )
            )
        except Exception as exc:
            self.records.append(
                ExtensionHealthRecord(
                    extension_id=extension.id,
                    kind=extension.kind,
                    enabled=True,
                    ok=False,
                    message=f"MCP startup failed: {exc}",
                )
            )
        finally:
            if handle is not None and all(
                record.extension_id != extension.id or record.ok is False
                for record in self.records[-1:]
            ):
                await handle.close()


class MCPToolExecutor:
    def __init__(
        self,
        *,
        handle: _MCPServerHandle,
        extension_id: str,
        source_tool_name: str,
    ) -> None:
        self._handle = handle
        self._extension_id = extension_id
        self._source_tool_name = source_tool_name

    async def __call__(self, call: ToolCall) -> ToolResult:
        try:
            result = await self._handle.call_tool(
                name=self._source_tool_name,
                arguments=dict(call.arguments),
            )
            payload = {
                "session_id": call.session_id,
                "extension_id": self._extension_id,
                "source_tool_name": self._source_tool_name,
                "mcp_content": serialize_mcp_content(getattr(result, "content", [])),
                "structured_content": serialize_mcp_value(
                    getattr(result, "structuredContent", None)
                ),
                "is_error": bool(getattr(result, "isError", False)),
            }
            is_error = bool(getattr(result, "isError", False))
            return ToolResult(
                ok=not is_error,
                name=call.name,
                call_id=call.call_id,
                payload=payload,
                error_code="MCP_TOOL_ERROR" if is_error else None,
                error_message="MCP tool returned an error." if is_error else None,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                name=call.name,
                call_id=call.call_id,
                payload={"session_id": call.session_id, "extension_id": self._extension_id},
                error_code="MCP_TOOL_EXECUTION_FAILED",
                error_message=str(exc),
            )


def settings_value(key: str) -> str | None:
    import os

    raw = os.getenv(key)
    if raw is None:
        return None
    return raw.strip() or None


def resolve_tool_contributor(extension: InstalledExtension) -> ToolContributor:
    if extension.tool_package is None:
        raise RuntimeError("tool_package extension missing tool_package spec")

    spec = extension.tool_package
    if spec.entry_point:
        contributor = load_symbol(spec.entry_point)
        return coerce_contributor(contributor, extension_id=extension.id)

    eps = entry_points(group="portworld.tool_contributors")
    target = list(eps)
    if spec.package_ref:
        normalized_ref = spec.package_ref.strip().lower()
        target = [
            entry
            for entry in target
            if normalized_ref in entry.value.strip().lower()
            or entry.name.strip().lower() == normalized_ref
        ]
    if extension.package:
        package_name = extension.package.strip().lower()
        target = [
            entry
            for entry in target
            if (getattr(entry, "dist", None) is None)
            or (entry.dist is not None and entry.dist.metadata.get("Name", "").strip().lower() == package_name)
        ]
    if len(target) != 1:
        names = ", ".join(sorted(entry.name for entry in target))
        raise RuntimeError(
            "Expected exactly one matching entry point in group "
            f"'portworld.tool_contributors' for extension={extension.id}. matches=[{names}]"
        )
    contributor = target[0].load()
    return coerce_contributor(contributor, extension_id=extension.id)


def coerce_contributor(obj: Any, *, extension_id: str) -> ToolContributor:
    if callable(obj):
        candidate = obj
    else:
        raise RuntimeError(f"Extension {extension_id} contributor is not callable.")

    def wrapped(registry: RealtimeToolRegistry, context: Any) -> None:
        try:
            candidate(registry=registry, context=context)
            return
        except TypeError:
            value = candidate()
        if callable(value):
            value(registry=registry, context=context)
            return
        if isinstance(value, Iterable):
            for contributor in value:
                if callable(contributor):
                    contributor(registry=registry, context=context)
            return
        raise RuntimeError(f"Extension {extension_id} contributor returned unsupported value.")

    return wrapped


def load_symbol(path: str) -> Any:
    if ":" not in path:
        raise RuntimeError(f"Invalid entrypoint format '{path}'. Use module:attribute.")
    module_name, attribute = path.split(":", 1)
    module = importlib.import_module(module_name)
    value = getattr(module, attribute, None)
    if value is None:
        raise RuntimeError(f"Could not resolve symbol '{attribute}' from module '{module_name}'.")
    return value


async def open_mcp_handle(*, extension_id: str, spec: MCPServerSpec) -> _MCPServerHandle:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    if spec.transport == "streamable_http":
        return await _open_streamable_http_handle(
            extension_id=extension_id,
            spec=spec,
            session_factory=ClientSession,
            transport_factory=streamablehttp_client,
        )

    exit_stack = AsyncExitStack()
    await exit_stack.__aenter__()
    try:
        env: dict[str, str] = {}
        for target_key, source_key in parse_env_bindings(spec.env_bindings).items():
            env[target_key] = settings_value(source_key) or ""
        resolved_command, resolved_args, endpoint_label = resolve_stdio_launcher(spec)
        server = StdioServerParameters(
            command=resolved_command,
            args=list(resolved_args),
            env=env or None,
            cwd=spec.cwd,
        )
        read_stream, write_stream = await exit_stack.enter_async_context(stdio_client(server))
        session = ClientSession(read_stream, write_stream)
        session = await exit_stack.enter_async_context(session)
        await asyncio.wait_for(
            session.initialize(),
            timeout=max(1.0, spec.startup_timeout_seconds),
        )
        return _MCPServerHandle(
            extension_id=extension_id,
            endpoint_label=endpoint_label,
            session=session,
            exit_stack=exit_stack,
        )
    except Exception:
        await exit_stack.aclose()
        raise


async def _open_streamable_http_handle(
    *,
    extension_id: str,
    spec: MCPServerSpec,
    session_factory: Any,
    transport_factory: Any,
) -> _MCPServerHandle:
    url = (spec.url or "").strip()
    headers = resolve_http_headers(spec)
    timeout_seconds = max(1.0, spec.startup_timeout_seconds)
    last_error: MCPStartupError | None = None

    for attempt in range(1, STREAMABLE_HTTP_CONNECT_ATTEMPTS + 1):
        exit_stack = AsyncExitStack()
        await exit_stack.__aenter__()
        try:
            streamable = await exit_stack.enter_async_context(
                transport_factory(
                    url,
                    headers=headers or None,
                    timeout=timeout_seconds,
                )
            )
            read_stream, write_stream = streamable[0], streamable[1]
            session = session_factory(read_stream, write_stream)
            session = await exit_stack.enter_async_context(session)
            await asyncio.wait_for(
                session.initialize(),
                timeout=timeout_seconds,
            )
            return _MCPServerHandle(
                extension_id=extension_id,
                endpoint_label=f"http:{url}",
                session=session,
                exit_stack=exit_stack,
            )
        except Exception as exc:
            await exit_stack.aclose()
            last_error = classify_streamable_http_exception(
                exc,
                url=url,
                phase="connect",
                attempt=attempt,
                attempts=STREAMABLE_HTTP_CONNECT_ATTEMPTS,
                timeout_seconds=timeout_seconds,
            )
            if not should_retry_streamable_http_error(last_error, attempt=attempt):
                raise last_error
            await asyncio.sleep(STREAMABLE_HTTP_RETRY_DELAY_SECONDS)

    assert last_error is not None
    raise last_error


async def list_mcp_tools(*, handle: _MCPServerHandle, spec: MCPServerSpec) -> Any:
    timeout_seconds = max(1.0, spec.startup_timeout_seconds)
    try:
        return await asyncio.wait_for(
            handle.session.list_tools(),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        if spec.transport == "streamable_http":
            raise classify_streamable_http_exception(
                exc,
                url=handle.endpoint_label.removeprefix("http:"),
                phase="tool_discovery",
                attempt=1,
                attempts=1,
                timeout_seconds=timeout_seconds,
            ) from exc
        raise MCPStartupError(
            phase="tool_discovery",
            reason="tool_discovery_failed",
            message=f"MCP tool discovery failed for {handle.endpoint_label}: {exc}",
            details={"endpoint_label": handle.endpoint_label, "transport": spec.transport},
        ) from exc


def resolve_stdio_launcher(spec: MCPServerSpec) -> tuple[str, tuple[str, ...], str]:
    launcher = (spec.launcher or "direct").strip().lower()
    package_spec = _format_package_spec(spec.package, spec.package_version)

    if launcher == "direct":
        command = (spec.command or "").strip()
        if not command:
            raise RuntimeError("MCP stdio launcher=direct requires a non-empty command.")
        _require_executable(command=command, launcher=launcher)
        return command, tuple(spec.args), f"stdio:{command}"

    if launcher == "npx":
        if not package_spec:
            raise RuntimeError("MCP stdio launcher=npx requires mcp_server.package.")
        command = "npx"
        _require_executable(command=command, launcher=launcher)
        args = ("-y", package_spec, *spec.args)
        return command, args, f"stdio:npx:{package_spec}"

    if launcher == "npm_exec":
        if not package_spec:
            raise RuntimeError("MCP stdio launcher=npm_exec requires mcp_server.package.")
        command = "npm"
        _require_executable(command=command, launcher=launcher)
        args = ("exec", "--", package_spec, *spec.args)
        return command, args, f"stdio:npm_exec:{package_spec}"

    raise RuntimeError(
        f"Unsupported MCP stdio launcher={launcher!r}. Supported launchers: direct, npx, npm_exec."
    )


def resolve_http_headers(spec: MCPServerSpec) -> dict[str, str]:
    bindings = parse_header_bindings(spec.headers_from_env)
    headers: dict[str, str] = {}
    missing_sources: list[str] = []
    missing_headers: list[str] = []
    for header_name, source_key in bindings.items():
        resolved = settings_value(source_key)
        if resolved is None:
            missing_sources.append(source_key)
            missing_headers.append(header_name)
            continue
        headers[header_name] = resolved
    if missing_sources:
        raise MCPStartupError(
            phase="connect",
            reason="auth_config",
            message=(
                "Remote MCP auth headers reference missing environment values: "
                + ", ".join(sorted(set(missing_sources)))
            ),
            details={
                "missing_env_keys": sorted(set(missing_sources)),
                "missing_headers": sorted(set(missing_headers)),
                "transport": spec.transport,
                "url": spec.url,
            },
        )
    return headers


def should_retry_streamable_http_error(error: MCPStartupError, *, attempt: int) -> bool:
    if attempt >= STREAMABLE_HTTP_CONNECT_ATTEMPTS:
        return False
    return error.reason not in {"auth_config", "auth_rejected"}


def classify_streamable_http_exception(
    exc: Exception,
    *,
    url: str,
    phase: str,
    attempt: int,
    attempts: int,
    timeout_seconds: float,
) -> MCPStartupError:
    if isinstance(exc, MCPStartupError):
        return exc

    detail = str(exc).strip() or type(exc).__name__
    lowered = detail.lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return MCPStartupError(
            phase=phase,
            reason="timeout",
            message=(
                f"Remote MCP {phase} timed out after {timeout_seconds:.1f}s for {url} "
                f"(attempt {attempt}/{attempts})."
            ),
            details={"url": url, "attempt": attempt, "attempts": attempts},
        )
    if isinstance(exc, httpx.HTTPStatusError):
        reason = "auth_rejected" if exc.response.status_code in {401, 403} else "transport_http"
        return MCPStartupError(
            phase=phase,
            reason=reason,
            message=(
                f"Remote MCP {phase} returned HTTP {exc.response.status_code} for {url}: {detail}"
            ),
            details={
                "url": url,
                "http_status": exc.response.status_code,
                "attempt": attempt,
                "attempts": attempts,
            },
        )
    if isinstance(exc, httpx.HTTPError):
        return MCPStartupError(
            phase=phase,
            reason="transport_http",
            message=f"Remote MCP {phase} transport failed for {url}: {detail}",
            details={"url": url, "attempt": attempt, "attempts": attempts},
        )
    if any(token in lowered for token in ("401", "403", "unauthorized", "forbidden", "authentication", "auth")):
        return MCPStartupError(
            phase=phase,
            reason="auth_rejected",
            message=f"Remote MCP {phase} rejected authentication for {url}: {detail}",
            details={"url": url, "attempt": attempt, "attempts": attempts},
        )
    return MCPStartupError(
        phase=phase,
        reason="transport_failed",
        message=f"Remote MCP {phase} failed for {url}: {detail}",
        details={"url": url, "attempt": attempt, "attempts": attempts},
    )


def collect_runtime_prerequisites(manifest: ExtensionManifest) -> ExtensionRuntimePrerequisites:
    node_launcher_enabled_count = 0
    required_binaries: set[str] = set()

    for extension in manifest.extensions:
        if not extension.enabled or extension.kind != "mcp_server" or extension.mcp_server is None:
            continue
        if extension.mcp_server.transport != "stdio":
            continue
        launcher = (extension.mcp_server.launcher or "direct").strip().lower()
        if launcher == "npx":
            node_launcher_enabled_count += 1
            required_binaries.update(("node", "npm", "npx"))
        elif launcher == "npm_exec":
            node_launcher_enabled_count += 1
            required_binaries.update(("node", "npm"))

    ordered_required = tuple(sorted(required_binaries))
    missing_binaries = tuple(
        binary for binary in ordered_required if shutil.which(binary) is None
    )
    return ExtensionRuntimePrerequisites(
        node_launcher_enabled_count=node_launcher_enabled_count,
        required_binaries=ordered_required,
        missing_binaries=missing_binaries,
    )


def runtime_missing_binaries_for_spec(spec: MCPServerSpec) -> tuple[str, ...]:
    if spec.transport != "stdio":
        return ()

    launcher = (spec.launcher or "direct").strip().lower()
    required: tuple[str, ...]
    if launcher == "npx":
        required = ("node", "npm", "npx")
    elif launcher == "npm_exec":
        required = ("node", "npm")
    else:
        return ()

    return tuple(binary for binary in required if shutil.which(binary) is None)


def _format_package_spec(package: str | None, package_version: str | None) -> str:
    normalized_package = (package or "").strip()
    normalized_version = (package_version or "").strip()
    if not normalized_package:
        return ""
    if not normalized_version:
        return normalized_package
    return f"{normalized_package}@{normalized_version}"


def _require_executable(*, command: str, launcher: str) -> None:
    if shutil.which(command) is not None:
        return
    raise RuntimeError(
        f"MCP stdio launcher={launcher} requires `{command}` on PATH, but it was not found."
    )


def serialize_mcp_content(content_items: Any) -> list[dict[str, Any]]:
    if not isinstance(content_items, list):
        return []
    serialized: list[dict[str, Any]] = []
    for item in content_items:
        serialized.append(serialize_mcp_value(item))
    return serialized


def serialize_mcp_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): serialize_mcp_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_mcp_value(item) for item in value]
    return value


def parse_env_bindings(bindings: tuple[str, ...]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for binding in bindings:
        if "=" in binding:
            target, source = binding.split("=", 1)
        elif ":" in binding:
            target, source = binding.split(":", 1)
        else:
            target = binding
            source = binding
        target = target.strip()
        source = source.strip()
        if target and source:
            resolved[target] = source
    return resolved


def parse_header_bindings(bindings: tuple[str, ...]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for binding in bindings:
        if "=" in binding:
            header, source = binding.split("=", 1)
        elif ":" in binding:
            header, source = binding.split(":", 1)
        else:
            source = binding.strip()
            header = source
        header = header.strip()
        source = source.strip()
        if header and source:
            resolved[header] = source
    return resolved

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import httpx

from portworld_cli.extensions.manifest import (
    ExtensionManifestError,
    get_local_definition,
    load_manifest,
)
from portworld_cli.extensions.types import (
    EXTENSION_KIND_MCP_SERVER,
    EXTENSION_KIND_TOOL_PACKAGE,
    MCP_LAUNCHER_DIRECT,
    MCP_LAUNCHER_NPM_EXEC,
    MCP_LAUNCHER_NPX,
    MCP_TRANSPORT_STDIO,
    MCP_TRANSPORT_STREAMABLE_HTTP,
)
from portworld_cli.output import DiagnosticCheck

INSTALLER_REMEDIATION_ACTION = (
    "Install missing runtime prerequisites with `bash install.sh --no-init --non-interactive`, "
    "then rerun `portworld extensions doctor`."
)
REMOTE_MCP_DOCTOR_TIMEOUT_SECONDS = 3.0
REMOTE_MCP_PROBE_PASS_STATUS_CODES = {200, 202, 204, 400, 405, 406, 415}


@dataclass(frozen=True, slots=True)
class ExtensionDoctorResult:
    checks: tuple[DiagnosticCheck, ...]
    extension_count: int
    enabled_count: int

    @property
    def ok(self) -> bool:
        return all(check.status != "fail" for check in self.checks)

    def to_payload(self) -> dict[str, object]:
        return {
            "extension_count": self.extension_count,
            "enabled_count": self.enabled_count,
            "checks": [check.to_dict() for check in self.checks],
        }


def run_extension_doctor(
    *,
    manifest_path: Path,
    python_install_dir: Path,
    extension_id: str | None = None,
) -> ExtensionDoctorResult:
    checks: list[DiagnosticCheck] = []
    try:
        manifest = load_manifest(manifest_path)
    except ExtensionManifestError as exc:
        return ExtensionDoctorResult(
            checks=(
                DiagnosticCheck(
                    id="extensions_manifest_parse",
                    status="fail",
                    message=str(exc),
                    action="Fix `.portworld/extensions.json` and rerun.",
                ),
            ),
            extension_count=0,
            enabled_count=0,
        )

    checks.append(
        DiagnosticCheck(
            id="extensions_manifest_loaded",
            status="pass",
            message=f"Loaded extension manifest: {manifest_path}",
        )
    )

    python_dir_exists = python_install_dir.is_dir()
    checks.append(
        DiagnosticCheck(
            id="extensions_python_dir_exists",
            status="pass" if python_dir_exists else "warn",
            message=(
                f"Python extension install dir present: {python_install_dir}"
                if python_dir_exists
                else f"Python extension install dir missing: {python_install_dir}"
            ),
            action=(
                None
                if python_dir_exists
                else "Run `portworld extensions add ...` to create/reconcile the install dir."
            ),
        )
    )

    selected_id = None if extension_id is None else extension_id.strip().lower()
    installed = manifest.installed
    if selected_id:
        installed = tuple(entry for entry in installed if entry.id == selected_id)
        if not installed:
            checks.append(
                DiagnosticCheck(
                    id="extension_selected_exists",
                    status="fail",
                    message=f"Extension is not installed: {selected_id}",
                )
            )
            return ExtensionDoctorResult(
                checks=tuple(checks),
                extension_count=len(manifest.installed),
                enabled_count=sum(1 for item in manifest.installed if item.enabled),
            )

    for entry in installed:
        checks.extend(_validate_installed_entry(entry=entry, manifest=manifest))

    return ExtensionDoctorResult(
        checks=tuple(checks),
        extension_count=len(manifest.installed),
        enabled_count=sum(1 for item in manifest.installed if item.enabled),
    )


def _validate_installed_entry(*, entry, manifest) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    checks.append(
        DiagnosticCheck(
            id=f"extension_{entry.id}_enabled",
            status="pass" if entry.enabled else "warn",
            message=("enabled" if entry.enabled else "disabled"),
        )
    )
    if entry.source == "local" and get_local_definition(manifest, entry.id) is None:
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_local_definition",
                status="fail",
                message="Local extension is installed but local definition is missing.",
                action=f"Re-add extension `{entry.id}` with `portworld extensions add <path>`.",
            )
        )

    if entry.kind == EXTENSION_KIND_TOOL_PACKAGE:
        checks.extend(_validate_tool_package(entry))
    elif entry.kind == EXTENSION_KIND_MCP_SERVER:
        checks.extend(_validate_mcp_server(entry))
    return tuple(checks)


def _validate_tool_package(entry) -> tuple[DiagnosticCheck, ...]:
    if entry.tool_package is None:
        return (
            DiagnosticCheck(
                id=f"extension_{entry.id}_tool_package_spec",
                status="fail",
                message="tool_package spec missing.",
            ),
        )
    package_ref = entry.tool_package.package_ref.strip()
    return (
        DiagnosticCheck(
            id=f"extension_{entry.id}_tool_package_ref",
            status="pass" if package_ref else "fail",
            message=(
                f"package_ref={package_ref}"
                if package_ref
                else "tool_package.package_ref must be non-empty."
            ),
        ),
    )


def _validate_mcp_server(entry) -> tuple[DiagnosticCheck, ...]:
    if entry.mcp_server is None:
        return (
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_spec",
                status="fail",
                message="mcp_server spec missing.",
            ),
        )
    transport = entry.mcp_server.transport.strip()
    launcher = entry.mcp_server.launcher.strip() or MCP_LAUNCHER_DIRECT
    checks: list[DiagnosticCheck] = []
    if transport not in {MCP_TRANSPORT_STDIO, MCP_TRANSPORT_STREAMABLE_HTTP}:
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_transport",
                status="fail",
                message=f"Unsupported transport: {transport!r}",
            )
        )
        return tuple(checks)

    checks.append(
        DiagnosticCheck(
            id=f"extension_{entry.id}_mcp_transport",
            status="pass",
            message=f"transport={transport}",
        )
    )
    if transport == MCP_TRANSPORT_STDIO:
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_stdio_launcher",
                status=(
                    "pass"
                    if launcher in {MCP_LAUNCHER_DIRECT, MCP_LAUNCHER_NPX, MCP_LAUNCHER_NPM_EXEC}
                    else "fail"
                ),
                message=f"launcher={launcher}",
            )
        )
        if launcher == MCP_LAUNCHER_DIRECT:
            command = (entry.mcp_server.command or "").strip()
            if not command:
                checks.append(
                    DiagnosticCheck(
                        id=f"extension_{entry.id}_mcp_stdio_command",
                        status="fail",
                        message="mcp_server.command is required for stdio transport with launcher=direct.",
                    )
                )
            elif shutil.which(command) is None:
                checks.append(
                    DiagnosticCheck(
                        id=f"extension_{entry.id}_mcp_stdio_command",
                        status="warn",
                        message=f"Command not found on PATH: {command}",
                        action=(
                            "Install or expose the command on PATH, or use the PortWorld bootstrap "
                            f"installer. {INSTALLER_REMEDIATION_ACTION}"
                        ),
                    )
                )
            else:
                checks.append(
                    DiagnosticCheck(
                        id=f"extension_{entry.id}_mcp_stdio_command",
                        status="pass",
                        message=f"Resolved command on PATH: {command}",
                    )
                )
        elif launcher in {MCP_LAUNCHER_NPX, MCP_LAUNCHER_NPM_EXEC}:
            checks.extend(_validate_node_launcher_prerequisites(entry, launcher=launcher))
        else:
            checks.append(
                DiagnosticCheck(
                    id=f"extension_{entry.id}_mcp_stdio_launcher",
                    status="fail",
                    message=(
                        "Unsupported launcher. Expected one of: "
                        f"{MCP_LAUNCHER_DIRECT}, {MCP_LAUNCHER_NPX}, {MCP_LAUNCHER_NPM_EXEC}."
                    ),
                )
            )
    if transport == MCP_TRANSPORT_STREAMABLE_HTTP:
        url = (entry.mcp_server.url or "").strip()
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_http_url",
                status="pass" if url else "fail",
                message=(f"url={url}" if url else "mcp_server.url is required for streamable_http transport."),
            )
        )
        if url:
            checks.extend(_validate_streamable_http_server(entry, url=url))
    return tuple(checks)


def _validate_node_launcher_prerequisites(entry, *, launcher: str) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    package = (entry.mcp_server.package or "").strip()
    checks.append(
        DiagnosticCheck(
            id=f"extension_{entry.id}_mcp_node_package",
            status="pass" if package else "fail",
            message=(
                f"package={package}"
                if package
                else f"mcp_server.package is required for launcher={launcher}."
            ),
        )
    )

    node_present = shutil.which("node") is not None
    checks.append(
        DiagnosticCheck(
            id=f"extension_{entry.id}_mcp_node_binary",
            status="pass" if node_present else "warn",
            message=(
                "Resolved command on PATH: node"
                if node_present
                else "Command not found on PATH: node"
            ),
            action=None if node_present else INSTALLER_REMEDIATION_ACTION,
        )
    )

    npm_present = shutil.which("npm") is not None
    checks.append(
        DiagnosticCheck(
            id=f"extension_{entry.id}_mcp_npm_binary",
            status="pass" if npm_present else "warn",
            message=(
                "Resolved command on PATH: npm"
                if npm_present
                else "Command not found on PATH: npm"
            ),
            action=None if npm_present else INSTALLER_REMEDIATION_ACTION,
        )
    )

    if launcher == MCP_LAUNCHER_NPX:
        npx_present = shutil.which("npx") is not None
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_npx_binary",
                status="pass" if npx_present else "warn",
                message=(
                    "Resolved command on PATH: npx"
                    if npx_present
                    else "Command not found on PATH: npx"
                ),
                action=None if npx_present else INSTALLER_REMEDIATION_ACTION,
            )
        )
    return tuple(checks)


def _validate_streamable_http_server(entry, *, url: str) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    header_bindings = _parse_header_bindings(entry.mcp_server.headers_from_env)
    if not header_bindings:
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_http_headers",
                status="pass",
                message="No auth header bindings configured.",
            )
        )
        headers: dict[str, str] = {}
    else:
        headers = {}
        missing_env_keys: list[str] = []
        for header_name, env_key in header_bindings.items():
            resolved = (os.getenv(env_key) or "").strip()
            if not resolved:
                missing_env_keys.append(env_key)
                continue
            headers[header_name] = resolved
        if missing_env_keys:
            checks.append(
                DiagnosticCheck(
                    id=f"extension_{entry.id}_mcp_http_headers",
                    status="fail",
                    message=(
                        "Missing env values for remote MCP auth headers: "
                        + ", ".join(sorted(set(missing_env_keys)))
                    ),
                    action=(
                        "Set the missing auth env vars for the remote MCP endpoint, "
                        "then rerun `portworld extensions doctor`."
                    ),
                )
            )
            return tuple(checks)
        checks.append(
            DiagnosticCheck(
                id=f"extension_{entry.id}_mcp_http_headers",
                status="pass",
                message=(
                    "Resolved auth header bindings: "
                    + ", ".join(sorted(header_bindings))
                ),
            )
        )

    checks.append(_probe_streamable_http_reachability(entry.id, url=url, headers=headers))
    return tuple(checks)


def _probe_streamable_http_reachability(
    extension_id: str,
    *,
    url: str,
    headers: dict[str, str],
) -> DiagnosticCheck:
    try:
        response = httpx.get(
            url,
            headers=headers or None,
            timeout=REMOTE_MCP_DOCTOR_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        return DiagnosticCheck(
            id=f"extension_{extension_id}_mcp_http_reachability",
            status="warn",
            message=(
                f"Remote MCP endpoint timed out after {REMOTE_MCP_DOCTOR_TIMEOUT_SECONDS:.1f}s: {url}"
            ),
            action="Verify the remote MCP URL, service availability, and network access, then rerun `portworld extensions doctor`.",
        )
    except httpx.HTTPError as exc:
        return DiagnosticCheck(
            id=f"extension_{extension_id}_mcp_http_reachability",
            status="warn",
            message=f"Remote MCP endpoint was not reachable: {exc}",
            action="Verify the remote MCP URL, service availability, and network access, then rerun `portworld extensions doctor`.",
        )

    if response.status_code in REMOTE_MCP_PROBE_PASS_STATUS_CODES:
        return DiagnosticCheck(
            id=f"extension_{extension_id}_mcp_http_reachability",
            status="pass",
            message=f"Remote MCP endpoint responded to probe with HTTP {response.status_code}: {url}",
        )

    if response.status_code in {401, 403}:
        return DiagnosticCheck(
            id=f"extension_{extension_id}_mcp_http_reachability",
            status="warn",
            message=(
                f"Remote MCP endpoint is reachable but rejected the probe with HTTP {response.status_code}: {url}"
            ),
            action="Verify the configured auth header env vars and remote MCP authorization rules, then rerun `portworld extensions doctor`.",
        )

    return DiagnosticCheck(
        id=f"extension_{extension_id}_mcp_http_reachability",
        status="warn",
        message=(
            f"Remote MCP endpoint is reachable but returned unexpected HTTP {response.status_code}: {url}"
        ),
        action="Verify that mcp_server.url points to the correct streamable HTTP MCP endpoint, then rerun `portworld extensions doctor`.",
    )


def _parse_header_bindings(bindings: tuple[str, ...]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for binding in bindings:
        if "=" in binding:
            header, source = binding.split("=", 1)
        elif ":" in binding:
            header, source = binding.split(":", 1)
        else:
            header = binding
            source = binding
        header = header.strip()
        source = source.strip()
        if header and source:
            resolved[header] = source
    return resolved

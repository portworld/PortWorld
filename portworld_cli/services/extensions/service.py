from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portworld_cli.context import CLIContext
from portworld_cli.envfile import parse_env_file, write_canonical_env
from portworld_cli.extensions import (
    EXTENSION_SOURCE_CATALOG,
    EXTENSION_SOURCE_LOCAL,
    ExtensionManifestError,
    InstalledExtension,
    build_extension_runtime_env_overrides,
    ensure_manifest_file,
    list_official_extensions,
    load_local_definition_file,
    load_manifest,
    reconcile_python_extension_install_dir,
    remove_installed_extension,
    resolve_official_extension,
    run_extension_doctor,
    set_installed_enabled,
    upsert_installed_extension,
    upsert_local_definition,
    write_manifest,
)
from portworld_cli.output import CommandResult, DiagnosticCheck, format_key_value_lines
from portworld_cli.services.common import ErrorMappingPolicy, map_command_exception
from portworld_cli.workspace.project_config import (
    ProjectConfig,
    ToolingConfig,
    build_env_overrides_from_project_config,
    write_project_config,
)
from portworld_cli.workspace.session import load_workspace_session


COMMAND_NAME = "portworld extensions"
ERROR_POLICY = ErrorMappingPolicy(command_name=COMMAND_NAME)


class ExtensionUsageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _WorkspacePersistResult:
    runtime_env_overrides: dict[str, str]
    tooling_auto_enabled: bool


def run_extensions_list(cli_context: CLIContext) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
        manifest = load_manifest(session.workspace_paths.extensions_manifest_file)
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name="portworld extensions list"),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
        )

    installed_by_id = {entry.id: entry for entry in manifest.installed}
    catalog_payload = []
    for entry in list_official_extensions():
        installed = installed_by_id.get(entry.id)
        catalog_payload.append(
            {
                **entry.to_payload(),
                "installed": installed is not None,
                "enabled": None if installed is None else installed.enabled,
                "source": None if installed is None else installed.source,
            }
        )

    installed_payload = [entry.to_payload() for entry in manifest.installed]
    env_overrides = build_extension_runtime_env_overrides(session)
    message = "\n".join(
        [
            format_key_value_lines(
                ("workspace_root", session.workspace_root),
                ("manifest_path", session.workspace_paths.extensions_manifest_file),
                ("install_dir", session.workspace_paths.extensions_python_dir),
                ("installed_count", len(manifest.installed)),
                ("enabled_count", sum(1 for item in manifest.installed if item.enabled)),
                ("official_catalog_count", len(list_official_extensions())),
            ),
            "installed_extensions:",
            *(
                [f"- {entry.id} ({entry.kind}, {'enabled' if entry.enabled else 'disabled'}, {entry.source})" for entry in manifest.installed]
                or ["- none"]
            ),
        ]
    )
    return CommandResult(
        ok=True,
        command="portworld extensions list",
        message=message,
        data={
            "workspace_root": str(session.workspace_root),
            "extensions_manifest_path": str(session.workspace_paths.extensions_manifest_file),
            "extensions_python_install_dir": str(session.workspace_paths.extensions_python_dir),
            "runtime_env_overrides": env_overrides,
            "installed": installed_payload,
            "official_catalog": catalog_payload,
        },
        exit_code=0,
    )


def run_extensions_show(cli_context: CLIContext, extension_id: str) -> CommandResult:
    normalized_id = extension_id.strip().lower()
    if not normalized_id:
        return _usage_error_result("portworld extensions show", "Extension id cannot be empty.")
    try:
        session = load_workspace_session(cli_context)
        manifest = load_manifest(session.workspace_paths.extensions_manifest_file)
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name="portworld extensions show"),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
        )

    catalog_entry = resolve_official_extension(normalized_id)
    installed_entry = next(
        (entry for entry in manifest.installed if entry.id == normalized_id),
        None,
    )
    local_entry = next(
        (entry for entry in manifest.local_definitions if entry.id == normalized_id),
        None,
    )
    if catalog_entry is None and installed_entry is None and local_entry is None:
        return _usage_error_result(
            "portworld extensions show",
            f"Unknown extension: {normalized_id}",
        )

    message_lines = [
        format_key_value_lines(
            ("workspace_root", session.workspace_root),
            ("extension_id", normalized_id),
            ("installed", installed_entry is not None),
            ("catalog_entry", catalog_entry is not None),
            ("local_definition", local_entry is not None),
        )
    ]
    if installed_entry is not None:
        message_lines.append(
            format_key_value_lines(
                ("kind", installed_entry.kind),
                ("source", installed_entry.source),
                ("enabled", installed_entry.enabled),
            )
        )
    return CommandResult(
        ok=True,
        command="portworld extensions show",
        message="\n".join(message_lines),
        data={
            "workspace_root": str(session.workspace_root),
            "extension_id": normalized_id,
            "installed": None if installed_entry is None else installed_entry.to_payload(),
            "catalog_entry": None if catalog_entry is None else catalog_entry.to_payload(),
            "local_definition": None if local_entry is None else local_entry.to_payload(),
        },
        exit_code=0,
    )


def run_extensions_add(cli_context: CLIContext, extension_ref: str) -> CommandResult:
    normalized_ref = extension_ref.strip()
    if not normalized_ref:
        return _usage_error_result("portworld extensions add", "Extension reference cannot be empty.")
    try:
        session = load_workspace_session(cli_context)
        manifest = ensure_manifest_file(session.workspace_paths.extensions_manifest_file)
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name="portworld extensions add"),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
        )

    previous_manifest = manifest
    try:
        catalog_entry = resolve_official_extension(normalized_ref.lower())
        if catalog_entry is not None:
            installed_entry = InstalledExtension(
                id=catalog_entry.id,
                kind=catalog_entry.kind,
                source=EXTENSION_SOURCE_CATALOG,
                enabled=True,
                required_env_keys=catalog_entry.required_env_keys,
                tool_package=catalog_entry.tool_package,
                mcp_server=catalog_entry.mcp_server,
                summary=catalog_entry.summary,
            )
            manifest = upsert_installed_extension(manifest, installed_entry)
            write_manifest(session.workspace_paths.extensions_manifest_file, manifest)
        else:
            definition_path = Path(normalized_ref).expanduser().resolve()
            if not definition_path.exists():
                raise ExtensionUsageError(
                    f"Unknown official extension id and local definition not found: {normalized_ref}"
                )
            local_definition = load_local_definition_file(definition_path)
            installed_entry = InstalledExtension(
                id=local_definition.id,
                kind=local_definition.kind,
                source=EXTENSION_SOURCE_LOCAL,
                enabled=True,
                required_env_keys=local_definition.required_env_keys,
                tool_package=local_definition.tool_package,
                mcp_server=local_definition.mcp_server,
                summary=local_definition.summary,
                install_metadata={"local_definition_path": str(definition_path)},
            )
            manifest = upsert_local_definition(manifest, local_definition)
            manifest = upsert_installed_extension(manifest, installed_entry)
            write_manifest(session.workspace_paths.extensions_manifest_file, manifest)

        install_summary = reconcile_python_extension_install_dir(
            manifest,
            install_dir=session.workspace_paths.extensions_python_dir,
        )
        updated_project_config, tooling_auto_enabled = _ensure_tooling_enabled(
            session.project_config,
            requested=True,
        )
        persist_result = _persist_workspace_settings(
            session,
            project_config=updated_project_config,
        )
    except Exception as exc:
        # Roll back manifest changes if add fails during install reconciliation.
        write_manifest(session.workspace_paths.extensions_manifest_file, previous_manifest)
        return map_command_exception(
            RuntimeError(f"Extension add failed and was rolled back. {exc}"),
            policy=ErrorMappingPolicy(command_name="portworld extensions add"),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
            include_common_exit_code_2=False,
        )
    return CommandResult(
        ok=True,
        command="portworld extensions add",
        message=format_key_value_lines(
            ("workspace_root", session.workspace_root),
            ("manifest_path", session.workspace_paths.extensions_manifest_file),
            ("install_dir", install_summary.install_dir),
            ("package_count", install_summary.package_count),
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "extensions_manifest_path": str(session.workspace_paths.extensions_manifest_file),
            "extensions_python_install_dir": str(session.workspace_paths.extensions_python_dir),
            "runtime_env_overrides": persist_result.runtime_env_overrides,
            "tooling_auto_enabled": tooling_auto_enabled,
            "manifest": manifest.to_payload(),
            "install_summary": install_summary.to_payload(),
        },
        exit_code=0,
    )


def run_extensions_remove(cli_context: CLIContext, extension_id: str) -> CommandResult:
    normalized_id = extension_id.strip().lower()
    if not normalized_id:
        return _usage_error_result("portworld extensions remove", "Extension id cannot be empty.")
    try:
        session = load_workspace_session(cli_context)
        manifest = ensure_manifest_file(session.workspace_paths.extensions_manifest_file)
        if not any(entry.id == normalized_id for entry in manifest.installed):
            raise ExtensionUsageError(f"Extension is not installed: {normalized_id}")
        updated_manifest = remove_installed_extension(manifest, normalized_id)
        write_manifest(session.workspace_paths.extensions_manifest_file, updated_manifest)
        install_summary = reconcile_python_extension_install_dir(
            updated_manifest,
            install_dir=session.workspace_paths.extensions_python_dir,
        )
        persist_result = _persist_workspace_settings(
            session,
            project_config=session.project_config,
        )
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name="portworld extensions remove"),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
        )

    return CommandResult(
        ok=True,
        command="portworld extensions remove",
        message=format_key_value_lines(
            ("extension_id", normalized_id),
            ("manifest_path", session.workspace_paths.extensions_manifest_file),
            ("install_dir", install_summary.install_dir),
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "extensions_manifest_path": str(session.workspace_paths.extensions_manifest_file),
            "runtime_env_overrides": persist_result.runtime_env_overrides,
            "tooling_auto_enabled": False,
            "manifest": updated_manifest.to_payload(),
            "install_summary": install_summary.to_payload(),
        },
        exit_code=0,
    )


def run_extensions_enable(
    cli_context: CLIContext,
    extension_id: str,
    *,
    enabled: bool,
) -> CommandResult:
    normalized_id = extension_id.strip().lower()
    command_name = "portworld extensions enable" if enabled else "portworld extensions disable"
    if not normalized_id:
        return _usage_error_result(command_name, "Extension id cannot be empty.")
    try:
        session = load_workspace_session(cli_context)
        manifest = ensure_manifest_file(session.workspace_paths.extensions_manifest_file)
        updated_manifest = set_installed_enabled(
            manifest,
            extension_id=normalized_id,
            enabled=enabled,
        )
        write_manifest(session.workspace_paths.extensions_manifest_file, updated_manifest)
        install_summary = reconcile_python_extension_install_dir(
            updated_manifest,
            install_dir=session.workspace_paths.extensions_python_dir,
        )
        updated_project_config, tooling_auto_enabled = _ensure_tooling_enabled(
            session.project_config,
            requested=enabled,
        )
        persist_result = _persist_workspace_settings(
            session,
            project_config=updated_project_config,
        )
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name=command_name),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
        )

    return CommandResult(
        ok=True,
        command=command_name,
        message=format_key_value_lines(
            ("extension_id", normalized_id),
            ("enabled", enabled),
            ("manifest_path", session.workspace_paths.extensions_manifest_file),
            ("package_count", install_summary.package_count),
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "extensions_manifest_path": str(session.workspace_paths.extensions_manifest_file),
            "runtime_env_overrides": persist_result.runtime_env_overrides,
            "tooling_auto_enabled": tooling_auto_enabled,
            "manifest": updated_manifest.to_payload(),
            "install_summary": install_summary.to_payload(),
        },
        exit_code=0,
    )


def run_extensions_disable(cli_context: CLIContext, extension_id: str) -> CommandResult:
    return run_extensions_enable(
        cli_context,
        extension_id,
        enabled=False,
    )


def run_extensions_doctor(
    cli_context: CLIContext,
    extension_id: str | None,
) -> CommandResult:
    try:
        session = load_workspace_session(cli_context)
        doctor_result = run_extension_doctor(
            manifest_path=session.workspace_paths.extensions_manifest_file,
            python_install_dir=session.workspace_paths.extensions_python_dir,
            extension_id=extension_id,
        )
        checks: list[DiagnosticCheck] = list(doctor_result.checks)
        if doctor_result.enabled_count > 0 and not session.project_config.providers.tooling.enabled:
            checks.append(
                DiagnosticCheck(
                    id="extensions_require_realtime_tooling",
                    status="fail",
                    message=(
                        "Extensions are enabled but REALTIME_TOOLING is disabled in project config."
                    ),
                    action="Run `portworld extensions enable <id>` again or set realtime tooling on via config/init.",
                )
            )
        ok = all(check.status != "fail" for check in checks)
    except Exception as exc:
        return map_command_exception(
            exc,
            policy=ErrorMappingPolicy(command_name="portworld extensions doctor"),
            usage_error_types=(ExtensionUsageError, ExtensionManifestError),
        )

    return CommandResult(
        ok=ok,
        command="portworld extensions doctor",
        message=format_key_value_lines(
            ("workspace_root", session.workspace_root),
            ("manifest_path", session.workspace_paths.extensions_manifest_file),
            ("extension_count", doctor_result.extension_count),
            ("enabled_count", doctor_result.enabled_count),
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "extensions_manifest_path": str(session.workspace_paths.extensions_manifest_file),
            "extensions_python_install_dir": str(session.workspace_paths.extensions_python_dir),
            "runtime_env_overrides": build_extension_runtime_env_overrides(session),
            "extension_count": doctor_result.extension_count,
            "enabled_count": doctor_result.enabled_count,
            "checks": [check.to_dict() for check in checks],
        },
        checks=tuple(checks),
        exit_code=0 if ok else 1,
    )


def _usage_error_result(command_name: str, message: str) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command_name,
        message=message,
        data={"status": "error", "error_type": "ExtensionUsageError"},
        exit_code=2,
    )


def _ensure_tooling_enabled(
    project_config: ProjectConfig,
    *,
    requested: bool,
) -> tuple[ProjectConfig, bool]:
    if not requested:
        return project_config, False
    if project_config.providers.tooling.enabled:
        return project_config, False
    updated_project_config = ProjectConfig(
        schema_version=project_config.schema_version,
        project_mode=project_config.project_mode,
        runtime_source=project_config.runtime_source,
        cloud_provider=project_config.cloud_provider,
        providers=type(project_config.providers)(
            realtime=project_config.providers.realtime,
            vision=project_config.providers.vision,
            tooling=ToolingConfig(
                enabled=True,
                web_search_provider=project_config.providers.tooling.web_search_provider,
            ),
        ),
        security=project_config.security,
        deploy=project_config.deploy,
    )
    return updated_project_config, True


def _persist_workspace_settings(
    session,
    *,
    project_config: ProjectConfig,
) -> _WorkspacePersistResult:
    runtime_env_overrides = build_extension_runtime_env_overrides(session)
    write_project_config(
        session.workspace_paths.project_config_file,
        project_config,
    )
    if session.template is None or session.env_path is None:
        return _WorkspacePersistResult(
            runtime_env_overrides=runtime_env_overrides,
            tooling_auto_enabled=False,
        )

    existing_env = session.existing_env
    if existing_env is None:
        existing_env = parse_env_file(session.env_path, template=session.template)
    config_overrides = build_env_overrides_from_project_config(project_config)
    known_overrides: dict[str, str] = {}
    custom_overrides: dict[str, str] = {}
    for key, value in runtime_env_overrides.items():
        if key in session.template.default_values:
            known_overrides[key] = value
        else:
            custom_overrides[key] = value
    for key, value in config_overrides.items():
        if key in session.template.default_values:
            known_overrides[key] = value
        else:
            custom_overrides[key] = value
    write_canonical_env(
        session.env_path,
        template=session.template,
        existing_env=existing_env,
        overrides=known_overrides,
        custom_overrides=custom_overrides,
    )
    return _WorkspacePersistResult(
        runtime_env_overrides=runtime_env_overrides,
        tooling_auto_enabled=(
            project_config.providers.tooling.enabled and not session.project_config.providers.tooling.enabled
        ),
    )

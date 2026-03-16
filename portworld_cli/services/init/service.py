from __future__ import annotations

from dataclasses import dataclass, replace

import click

from portworld_cli.config_runtime import (
    ConfigRuntimeError,
    ConfigSession,
    CloudEditOptions,
    ProviderEditOptions,
    RUNTIME_SOURCE_PUBLISHED,
    SecurityEditOptions,
    apply_cloud_section,
    apply_provider_section,
    apply_security_section,
    build_init_review_lines,
    build_init_success_message,
    collect_cloud_section,
    collect_provider_section,
    collect_security_section,
    confirm_apply,
    ensure_source_runtime_session,
    load_config_session,
    preview_secret_readiness,
    write_config_artifacts,
)
from portworld_cli.context import CLIContext
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.machine_state import load_machine_state, remember_active_workspace
from portworld_cli.output import CommandResult, DiagnosticCheck
from portworld_cli.paths import ProjectRootResolutionError, WorkspacePaths
from portworld_cli.project_config import (
    ProjectConfigError,
    RUNTIME_SOURCE_SOURCE,
    build_env_overrides_from_project_config,
)
from portworld_cli.published_workspace import (
    DEFAULT_PUBLISHED_HOST_PORT,
    PublishedWorkspaceTarget,
    load_published_env_template,
    prepare_published_workspace_root,
    render_published_compose,
    resolve_published_release_ref,
    resolve_published_workspace_target,
    write_published_workspace_artifacts,
)
from portworld_cli.state import CLIStateDecodeError, CLIStateTypeError
from portworld_cli.workspace.session import build_workspace_session


COMMAND_NAME = "portworld init"


@dataclass(frozen=True, slots=True)
class InitOptions:
    force: bool
    with_vision: bool
    without_vision: bool
    with_tooling: bool
    without_tooling: bool
    openai_api_key: str | None
    vision_provider_api_key: str | None
    tavily_api_key: str | None
    backend_profile: str | None
    cors_origins: str | None
    allowed_hosts: str | None
    bearer_token: str | None
    generate_bearer_token: bool
    clear_bearer_token: bool
    project_mode: str | None
    runtime_source: str | None
    stack_name: str | None
    release_tag: str | None
    host_port: int | None
    project: str | None
    region: str | None
    service: str | None
    artifact_repo: str | None
    sql_instance: str | None
    database: str | None
    bucket: str | None
    min_instances: int | None
    max_instances: int | None
    concurrency: int | None
    cpu: str | None
    memory: str | None


def run_init(cli_context: CLIContext, options: InitOptions) -> CommandResult:
    if options.runtime_source == RUNTIME_SOURCE_PUBLISHED:
        return _run_published_init(cli_context, options)
    if options.runtime_source == RUNTIME_SOURCE_SOURCE:
        return _run_source_init(cli_context, options)

    try:
        session = load_config_session(cli_context)
    except ProjectRootResolutionError:
        selected_runtime_source = _select_first_run_runtime_source(cli_context)
        if selected_runtime_source == RUNTIME_SOURCE_PUBLISHED:
            return _run_published_init(cli_context, options)
        return _source_init_requires_repo_result()
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        ConfigRuntimeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(exc, exit_code=2)
    except Exception as exc:
        return _failure_result(exc, exit_code=1)

    if session.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED:
        return _run_published_init(
            cli_context,
            options,
            existing_target=PublishedWorkspaceTarget(
                workspace_root=session.workspace_root,
                stack_name=session.workspace_root.name,
            ),
        )
    return _run_source_init(cli_context, options)


def _run_source_init(cli_context: CLIContext, options: InitOptions) -> CommandResult:
    try:
        session = load_config_session(cli_context)
        session = ensure_source_runtime_session(
            session,
            command_name=COMMAND_NAME,
            requested_runtime_source=options.runtime_source,
        )

        project_config, outcome = _collect_init_sections(
            session,
            options,
            runtime_source=options.runtime_source or RUNTIME_SOURCE_SOURCE,
        )
        confirm_apply(
            cli_context,
            command_name=COMMAND_NAME,
            env_path=session.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            summary_lines=outcome.review_lines,
            force=options.force,
        )
        write_outcome = write_config_artifacts(session, project_config, outcome.env_updates)
    except ProjectRootResolutionError as exc:
        if options.runtime_source == RUNTIME_SOURCE_SOURCE:
            return _source_init_requires_repo_result()
        return _failure_result(exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        ConfigRuntimeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(exc, exit_code=2)
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Aborted; configuration changes were not applied.",
            data={"status": "aborted", "error_type": "Abort"},
            exit_code=1,
        )
    except Exception as exc:
        return _failure_result(exc, exit_code=1)

    checks = _build_optional_secret_checks(
        project_config=write_outcome.project_config,
        tavily_present=write_outcome.secret_readiness.tavily_api_key_present,
        action="Run `portworld config edit providers` to add the missing optional credential.",
    )

    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=build_init_success_message(
            project_config=write_outcome.project_config,
            secret_readiness=write_outcome.secret_readiness,
            env_path=None if write_outcome.env_write_result is None else write_outcome.env_write_result.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            backup_path=None if write_outcome.env_write_result is None else write_outcome.env_write_result.backup_path,
            extra_lines=(
                f"workspace_root: {session.workspace_root}",
                f"project_root: {session.project_paths.project_root}",
            ),
            next_steps=(
                "next: portworld doctor --target local",
                "next: docker compose up --build",
                "next: portworld config show",
                "next: portworld deploy gcp-cloud-run",
            ),
        ),
        data={
            "workspace_root": str(session.workspace_root),
            "project_root": str(session.project_paths.project_root),
            "project_config_path": str(session.workspace_paths.project_config_file),
            "env_path": (
                None
                if write_outcome.env_write_result is None
                else str(write_outcome.env_write_result.env_path)
            ),
            "backup_path": (
                str(write_outcome.env_write_result.backup_path)
                if write_outcome.env_write_result is not None and write_outcome.env_write_result.backup_path
                else None
            ),
            "project_config": write_outcome.project_config.to_payload(),
            "secret_readiness": write_outcome.secret_readiness.to_dict(),
            "workspace_resolution_source": session.workspace_resolution_source,
            "active_workspace_root": (
                None if session.active_workspace_root is None else str(session.active_workspace_root)
            ),
        },
        checks=checks,
        exit_code=0,
    )


def _run_published_init(
    cli_context: CLIContext,
    options: InitOptions,
    *,
    existing_target: PublishedWorkspaceTarget | None = None,
) -> CommandResult:
    try:
        target = _resolve_published_target(
            cli_context,
            options,
            existing_target=existing_target,
        )
        workspace_paths = prepare_published_workspace_root(
            target,
            force=options.force,
        )
        session = _build_published_init_session(
            cli_context,
            workspace_paths=workspace_paths,
        )

        project_config, outcome = _collect_init_sections(
            session,
            options,
            runtime_source=RUNTIME_SOURCE_PUBLISHED,
        )
        release_ref = resolve_published_release_ref(options.release_tag)
        host_port = options.host_port or DEFAULT_PUBLISHED_HOST_PORT
        if host_port < 1 or host_port > 65535:
            raise ConfigRuntimeError("--host-port must be between 1 and 65535.")
        project_config = replace(
            project_config,
            runtime_source=RUNTIME_SOURCE_PUBLISHED,
            deploy=replace(
                project_config.deploy,
                published_runtime=replace(
                    project_config.deploy.published_runtime,
                    release_tag=release_ref.release_tag,
                    image_ref=release_ref.image_ref,
                    host_port=host_port,
                ),
            ),
        )
        preview_readiness = preview_secret_readiness(session, project_config, outcome.env_updates)
        confirm_apply(
            cli_context,
            command_name=COMMAND_NAME,
            env_path=workspace_paths.workspace_env_file,
            project_config_path=workspace_paths.project_config_file,
            summary_lines=outcome.review_lines
            + (
                f"workspace_root: {workspace_paths.workspace_root}",
                f"stack_name: {target.stack_name}",
                f"release_tag: {release_ref.release_tag}",
                f"image_ref: {release_ref.image_ref}",
                f"host_port: {host_port}",
            ),
            force=options.force,
        )
        env_write_result, compose_backup_path = write_published_workspace_artifacts(
            workspace_paths=workspace_paths,
            project_config=project_config,
            env_template=load_published_env_template(),
            env_overrides=_build_published_env_overrides(project_config, outcome.env_updates),
            compose_content=render_published_compose(
                image_ref=release_ref.image_ref,
                host_port=host_port,
            ),
            force=options.force,
        )
        machine_state = remember_active_workspace(workspace_paths.workspace_root)
        final_session = _build_published_init_session(
            cli_context,
            workspace_paths=workspace_paths,
        )
    except ProjectRootResolutionError as exc:
        return _failure_result(exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        ConfigRuntimeError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(exc, exit_code=2)
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Aborted; published workspace changes were not applied.",
            data={"status": "aborted", "error_type": "Abort"},
            exit_code=1,
        )
    except Exception as exc:
        return _failure_result(exc, exit_code=1)

    checks = _build_optional_secret_checks(
        project_config=project_config,
        tavily_present=final_session.secret_readiness().tavily_api_key_present,
        action="Rerun `portworld init --runtime-source published` to add the missing optional credential.",
    )
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message="\n".join(
            line
            for line in (
                build_init_success_message(
                    project_config=project_config,
                    secret_readiness=final_session.secret_readiness(),
                    env_path=env_write_result.env_path,
                    project_config_path=workspace_paths.project_config_file,
                    backup_path=env_write_result.backup_path,
                    extra_lines=(
                        f"workspace_root: {workspace_paths.workspace_root}",
                        f"compose_path: {workspace_paths.compose_file}",
                        (
                            f"compose_backup_path: {compose_backup_path}"
                            if compose_backup_path is not None
                            else None
                        ),
                        (
                            "active_workspace_default: yes"
                            if machine_state.active_workspace_root == workspace_paths.workspace_root
                            else None
                        ),
                    ),
                    next_steps=(
                        f"next: cd {workspace_paths.workspace_root}",
                        "next: docker compose up -d",
                        "next: portworld doctor --target local",
                        "next: portworld status",
                        "next: portworld deploy gcp-cloud-run",
                    ),
                ),
            )
            if line
        ),
        data={
            "workspace_root": str(workspace_paths.workspace_root),
            "project_root": None,
            "project_config_path": str(workspace_paths.project_config_file),
            "env_path": str(env_write_result.env_path),
            "compose_path": str(workspace_paths.compose_file),
            "backup_path": (
                str(env_write_result.backup_path)
                if env_write_result.backup_path is not None
                else None
            ),
            "compose_backup_path": (
                str(compose_backup_path) if compose_backup_path is not None else None
            ),
            "project_config": project_config.to_payload(),
            "secret_readiness": final_session.secret_readiness().to_dict(),
            "published_runtime": project_config.deploy.published_runtime.to_payload(),
            "workspace_resolution_source": final_session.workspace_resolution_source,
            "active_workspace_root": (
                None if machine_state.active_workspace_root is None else str(machine_state.active_workspace_root)
            ),
        },
        checks=checks,
        exit_code=0,
    )


@dataclass(frozen=True, slots=True)
class _InitCollectionOutcome:
    env_updates: dict[str, str]
    review_lines: tuple[str, ...]


def _collect_init_sections(
    session: ConfigSession,
    options: InitOptions,
    *,
    runtime_source: str,
) -> tuple[object, _InitCollectionOutcome]:
    provider_result = collect_provider_section(
        session,
        ProviderEditOptions(
            with_vision=options.with_vision,
            without_vision=options.without_vision,
            with_tooling=options.with_tooling,
            without_tooling=options.without_tooling,
            openai_api_key=options.openai_api_key,
            vision_provider_api_key=options.vision_provider_api_key,
            tavily_api_key=options.tavily_api_key,
        ),
    )
    project_config, env_updates = apply_provider_section(
        session.project_config,
        provider_result,
    )

    security_result = collect_security_section(
        _session_with_project_config(session, project_config),
        SecurityEditOptions(
            backend_profile=options.backend_profile,
            cors_origins=options.cors_origins,
            allowed_hosts=options.allowed_hosts,
            bearer_token=options.bearer_token,
            generate_bearer_token=options.generate_bearer_token,
            clear_bearer_token=options.clear_bearer_token,
        ),
    )
    project_config, security_env_updates = apply_security_section(
        project_config,
        security_result,
    )
    env_updates.update(security_env_updates)

    cloud_result = collect_cloud_section(
        _session_with_project_config(session, project_config),
        CloudEditOptions(
            project_mode=options.project_mode,
            runtime_source=runtime_source,
            project=options.project,
            region=options.region,
            service=options.service,
            artifact_repo=options.artifact_repo,
            sql_instance=options.sql_instance,
            database=options.database,
            bucket=options.bucket,
            min_instances=options.min_instances,
            max_instances=options.max_instances,
            concurrency=options.concurrency,
            cpu=options.cpu,
            memory=options.memory,
        ),
        prompt_defaults_when_local=False,
    )
    project_config, cloud_env_updates = apply_cloud_section(project_config, cloud_result)
    env_updates.update(cloud_env_updates)

    preview_outcome = preview_secret_readiness(session, project_config, env_updates)
    return project_config, _InitCollectionOutcome(
        env_updates=env_updates,
        review_lines=build_init_review_lines(
            project_config=project_config,
            secret_readiness=preview_outcome,
        ),
    )


def _build_published_init_session(
    cli_context: CLIContext,
    *,
    workspace_paths: WorkspacePaths,
) -> ConfigSession:
    active_workspace_root = load_machine_state().active_workspace_root
    return replace(
        build_workspace_session(
            replace(cli_context, project_root_override=workspace_paths.workspace_root),
            workspace_paths=workspace_paths,
            workspace_resolution_source=(
                "explicit"
                if cli_context.project_root_override is not None
                else (
                    "active_workspace"
                    if active_workspace_root == workspace_paths.workspace_root
                    else "cwd"
                )
            ),
            active_workspace_root=active_workspace_root,
        ),
        effective_runtime_source=RUNTIME_SOURCE_PUBLISHED,
    )


def _select_first_run_runtime_source(cli_context: CLIContext) -> str:
    if cli_context.non_interactive:
        return RUNTIME_SOURCE_PUBLISHED

    click.echo("How do you want to set up PortWorld?")
    click.echo("  operator: zero-clone workspace with published runtime images (recommended)")
    click.echo("  contributor: repo-backed source checkout workflow")
    selection = click.prompt(
        "Setup flow",
        type=click.Choice(["operator", "contributor"], case_sensitive=False),
        default="operator",
        show_choices=False,
    )
    return (
        RUNTIME_SOURCE_PUBLISHED
        if selection.strip().lower() == "operator"
        else RUNTIME_SOURCE_SOURCE
    )


def _resolve_published_target(
    cli_context: CLIContext,
    options: InitOptions,
    *,
    existing_target: PublishedWorkspaceTarget | None,
) -> PublishedWorkspaceTarget:
    if cli_context.project_root_override is not None or options.stack_name is not None:
        return resolve_published_workspace_target(
            explicit_root=cli_context.project_root_override,
            stack_name=options.stack_name,
        )
    if existing_target is not None:
        return existing_target
    return resolve_published_workspace_target(
        explicit_root=None,
        stack_name=None,
    )


def _source_init_requires_repo_result() -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=(
            "Contributor/source init requires a PortWorld source checkout. "
            "Run this command from the repo root, or rerun `portworld init` and choose the "
            "operator workspace flow."
        ),
        data={
            "status": "error",
            "error_type": "ProjectRootResolutionError",
        },
        checks=(
            DiagnosticCheck(
                id="project-root",
                status="fail",
                message="No PortWorld source checkout was detected for contributor setup.",
                action="Clone the repo and run `portworld init` there, or use the operator workspace flow.",
            ),
        ),
        exit_code=1,
    )


def _build_published_env_overrides(project_config, env_updates: dict[str, str]) -> dict[str, str]:
    overrides = build_env_overrides_from_project_config(project_config)
    overrides.update(env_updates)
    return overrides


def _session_with_project_config(session, project_config):
    return replace(
        session,
        project_config=project_config,
        configured_runtime_source=project_config.runtime_source,
        effective_runtime_source=project_config.runtime_source or session.effective_runtime_source,
        runtime_source_derived_from_legacy=False,
    )


def _build_optional_secret_checks(
    *,
    project_config,
    tavily_present: bool | None,
    action: str,
) -> tuple[DiagnosticCheck, ...]:
    checks: list[DiagnosticCheck] = []
    if project_config.providers.tooling.enabled and not tavily_present:
        checks.append(
            DiagnosticCheck(
                id="missing-tavily-api-key",
                status="warn",
                message="tavily-api-key is not configured yet.",
                action=action,
            )
        )
    return tuple(checks)


def _failure_result(exc: Exception, *, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=COMMAND_NAME,
        message=str(exc),
        data={
            "status": "error",
            "error_type": type(exc).__name__,
        },
        exit_code=exit_code,
    )

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import subprocess

import click

from portworld_cli.aws.deploy import run_deploy_aws_ecs_fargate
from portworld_cli.aws.stages import DeployAWSECSFargateOptions
from portworld_cli.azure.deploy import run_deploy_azure_container_apps
from portworld_cli.azure.stages import DeployAzureContainerAppsOptions
from portworld_cli.context import CLIContext
from portworld_cli.deploy.config import DeployGCPCloudRunOptions
from portworld_cli.deploy.service import run_deploy_gcp_cloud_run
from portworld_cli.envfile import EnvFileParseError
from portworld_cli.output import CommandResult, DiagnosticCheck
from portworld_cli.runtime.published import build_compose_command
from portworld_cli.services.config.edit_service import confirm_apply
from portworld_cli.services.config.errors import ConfigRuntimeError, ConfigUsageError
from portworld_cli.services.config.messages import build_init_confirmation_lines
from portworld_cli.services.config.persistence import preview_secret_readiness, write_config_artifacts
from portworld_cli.services.config.prompts import (
    resolve_required_text_value,
    resolve_secret_value,
)
from portworld_cli.services.config.sections import apply_security_section, collect_security_section
from portworld_cli.services.config.types import CloudSectionResult, SecurityEditOptions
from portworld_cli.targets import (
    CLOUD_PROVIDER_AWS,
    CLOUD_PROVIDER_AZURE,
    CLOUD_PROVIDER_GCP,
    TARGET_AWS_ECS_FARGATE,
    TARGET_AZURE_CONTAINER_APPS,
    TARGET_GCP_CLOUD_RUN,
)
from portworld_cli.ux.prompts import prompt_choice, prompt_confirm
from portworld_cli.workspace.config.providers import apply_provider_section, collect_provider_section
from portworld_cli.workspace.discovery.paths import (
    ProjectRootResolutionError,
    WorkspacePaths,
    resolve_project_paths,
)
from portworld_cli.workspace.project_config import (
    PROJECT_MODE_LOCAL,
    PROJECT_MODE_MANAGED,
    ProjectConfigError,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    build_env_overrides_from_project_config,
)
from portworld_cli.workspace.published import (
    DEFAULT_PUBLISHED_HOST_PORT,
    PublishedWorkspaceTarget,
    load_published_env_template,
    prepare_published_workspace_root,
    render_published_compose,
    resolve_published_release_ref,
    resolve_published_workspace_target,
    write_published_workspace_artifacts,
)
from portworld_cli.workspace.session import WorkspaceSession as ConfigSession
from portworld_cli.workspace.session import (
    build_workspace_session,
    load_workspace_session,
    require_source_workspace_session,
)
from portworld_cli.workspace.state.machine_state import load_machine_state, remember_active_workspace
from portworld_cli.workspace.state.state_store import CLIStateDecodeError, CLIStateTypeError
from portworld_shared.providers import (
    PROVIDER_KIND_REALTIME,
    PROVIDER_KIND_SEARCH,
    PROVIDER_KIND_VISION,
    get_provider_requirement,
)
from portworld_cli.providers.types import ProviderEditOptions


COMMAND_NAME = "portworld init"
SETUP_MODE_QUICKSTART = "quickstart"
SETUP_MODE_MANUAL = "manual"
LOCAL_TARGET = "local"


@dataclass(frozen=True, slots=True)
class InitOptions:
    force: bool
    realtime_provider: str | None
    with_vision: bool
    without_vision: bool
    vision_provider: str | None
    with_tooling: bool
    without_tooling: bool
    search_provider: str | None
    realtime_api_key: str | None
    vision_api_key: str | None
    search_api_key: str | None
    backend_profile: str | None
    bearer_token: str | None
    generate_bearer_token: bool
    clear_bearer_token: bool
    setup_mode: str | None
    project_mode: str | None
    runtime_source: str | None
    local_runtime: str | None
    cloud_provider: str | None
    target: str | None
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
    aws_region: str | None
    aws_service: str | None
    aws_vpc_id: str | None
    aws_subnet_ids: str | None
    azure_subscription: str | None
    azure_resource_group: str | None
    azure_region: str | None
    azure_environment: str | None
    azure_app: str | None


@dataclass(frozen=True, slots=True)
class _InteractiveInitDecision:
    options: InitOptions
    published_target: PublishedWorkspaceTarget | None
    prompt_session: ConfigSession | None


@dataclass(frozen=True, slots=True)
class _InitCollectionOutcome:
    env_updates: dict[str, str]
    review_lines: tuple[str, ...]


def run_init(cli_context: CLIContext, options: InitOptions) -> CommandResult:
    if not cli_context.non_interactive:
        try:
            decision = _collect_interactive_init_decision(cli_context, options)
        except click.Abort:
            return CommandResult(
                ok=False,
                command=COMMAND_NAME,
                message="Canceled before setup changes were applied.",
                data={"status": "aborted", "error_type": "Abort"},
                exit_code=1,
            )
        except ProjectRootResolutionError as exc:
            return _failure_result(exc, exit_code=1)
        except (
            CLIStateDecodeError,
            CLIStateTypeError,
            ConfigRuntimeError,
            ConfigUsageError,
            EnvFileParseError,
            ProjectConfigError,
        ) as exc:
            return _failure_result(exc, exit_code=2)
        except Exception as exc:
            return _failure_result(exc, exit_code=1)

        if decision.options.runtime_source == RUNTIME_SOURCE_PUBLISHED:
            return _run_published_init(
                cli_context,
                decision.options,
                existing_target=decision.published_target,
                onboarding_mode=True,
            )
        return _run_source_init(
            cli_context,
            decision.options,
            onboarding_mode=True,
        )

    options = replace(
        options,
        setup_mode=_resolve_setup_mode(cli_context, options),
        runtime_source=options.runtime_source or options.local_runtime,
    )
    if options.runtime_source == RUNTIME_SOURCE_PUBLISHED:
        return _run_published_init(cli_context, options)
    if options.runtime_source == RUNTIME_SOURCE_SOURCE:
        return _run_source_init(cli_context, options)

    try:
        session = load_workspace_session(cli_context)
    except ProjectRootResolutionError:
        selected_runtime_source = (
            RUNTIME_SOURCE_PUBLISHED
            if options.setup_mode == SETUP_MODE_QUICKSTART
            else _select_first_run_runtime_source(cli_context)
        )
        if selected_runtime_source == RUNTIME_SOURCE_PUBLISHED:
            return _run_published_init(cli_context, replace(options, runtime_source=RUNTIME_SOURCE_PUBLISHED))
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
            replace(options, runtime_source=RUNTIME_SOURCE_PUBLISHED),
            existing_target=PublishedWorkspaceTarget(
                workspace_root=session.workspace_root,
                stack_name=session.workspace_root.name,
            ),
        )
    return _run_source_init(
        cli_context,
        replace(options, runtime_source=RUNTIME_SOURCE_SOURCE),
    )


def _run_source_init(
    cli_context: CLIContext,
    options: InitOptions,
    *,
    onboarding_mode: bool = False,
) -> CommandResult:
    try:
        _emit_progress(cli_context, "Resolving source workspace.")
        session = load_workspace_session(cli_context)
        session = require_source_workspace_session(
            session,
            command_name=COMMAND_NAME,
            requested_runtime_source=options.runtime_source,
            usage_error_type=ConfigUsageError,
        )

        _emit_progress(cli_context, "Collecting setup choices.")
        project_config, outcome = _collect_init_sections(
            session,
            options,
            runtime_source=options.runtime_source or RUNTIME_SOURCE_SOURCE,
            onboarding_mode=onboarding_mode,
        )
        confirm_apply(
            cli_context,
            command_name=COMMAND_NAME,
            env_path=session.env_path,
            project_config_path=session.workspace_paths.project_config_file,
            summary_lines=(
                _build_onboarding_confirmation_lines(project_config=project_config)
                if onboarding_mode
                else outcome.review_lines + _planned_execution_lines(project_config)
            ),
            force=options.force,
            include_paths=False,
        )
        _emit_progress(cli_context, "Writing backend configuration.")
        write_outcome = write_config_artifacts(session, project_config, outcome.env_updates)
        _emit_progress(cli_context, "Running the selected setup steps.")
        execution = _execute_post_init(
            cli_context,
            options=options,
            session=_session_with_project_config(session, write_outcome.project_config),
            env_updates=outcome.env_updates,
        )
        if not execution.ok:
            return execution
    except ProjectRootResolutionError as exc:
        if options.runtime_source == RUNTIME_SOURCE_SOURCE:
            return _source_init_requires_repo_result()
        return _failure_result(exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        ConfigRuntimeError,
        ConfigUsageError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(exc, exit_code=2)
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Canceled. No files changed.",
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
        message=_build_final_success_message(
            project_config=write_outcome.project_config,
            secret_readiness=write_outcome.secret_readiness,
            project_config_path=session.workspace_paths.project_config_file,
            env_path=None if write_outcome.env_write_result is None else write_outcome.env_write_result.env_path,
            backup_path=None if write_outcome.env_write_result is None else write_outcome.env_write_result.backup_path,
            extra_lines=(
                f"workspace_root: {session.workspace_root}",
                f"project_root: {session.project_paths.project_root}",
                *_execution_summary_lines(execution.data),
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
            **_public_execution_data(execution.data),
        },
        checks=checks,
        exit_code=0,
    )


def _run_published_init(
    cli_context: CLIContext,
    options: InitOptions,
    *,
    existing_target: PublishedWorkspaceTarget | None = None,
    onboarding_mode: bool = False,
) -> CommandResult:
    try:
        _emit_progress(cli_context, "Preparing the local workspace.")
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

        _emit_progress(cli_context, "Collecting setup choices.")
        project_config, outcome = _collect_init_sections(
            session,
            options,
            runtime_source=RUNTIME_SOURCE_PUBLISHED,
            onboarding_mode=onboarding_mode,
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
            summary_lines=(
                _build_onboarding_confirmation_lines(
                    project_config=project_config,
                    workspace_root=workspace_paths.workspace_root,
                    stack_name=target.stack_name,
                )
                if onboarding_mode
                else outcome.review_lines
                + (
                    f"workspace_root: {workspace_paths.workspace_root}",
                    f"stack_name: {target.stack_name}",
                    f"release_tag: {release_ref.release_tag}",
                    f"image_ref: {release_ref.image_ref}",
                    f"host_port: {host_port}",
                    *_planned_execution_lines(project_config),
                )
            ),
            force=options.force,
            include_paths=False,
        )
        _emit_progress(cli_context, "Writing workspace configuration.")
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
        _emit_progress(cli_context, "Starting the selected setup steps.")
        execution = _execute_post_init(
            cli_context,
            options=options,
            session=_session_with_project_config(final_session, project_config),
            env_updates=outcome.env_updates,
        )
        if not execution.ok:
            return execution
    except ProjectRootResolutionError as exc:
        return _failure_result(exc, exit_code=1)
    except (
        CLIStateDecodeError,
        CLIStateTypeError,
        ConfigRuntimeError,
        ConfigUsageError,
        EnvFileParseError,
        ProjectConfigError,
    ) as exc:
        return _failure_result(exc, exit_code=2)
    except click.Abort:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Canceled. No published workspace files changed.",
            data={"status": "aborted", "error_type": "Abort"},
            exit_code=1,
        )
    except Exception as exc:
        return _failure_result(exc, exit_code=1)

    checks = _build_optional_secret_checks(
        project_config=project_config,
        tavily_present=preview_readiness.tavily_api_key_present,
        action="Rerun `portworld init --runtime-source published` to add the missing optional credential.",
    )
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=_build_final_success_message(
            project_config=project_config,
            secret_readiness=final_session.secret_readiness(),
            project_config_path=workspace_paths.project_config_file,
            env_path=env_write_result.env_path,
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
                *_execution_summary_lines(execution.data),
            ),
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
            **_public_execution_data(execution.data),
        },
        checks=checks,
        exit_code=0,
    )


def _collect_init_sections(
    session: ConfigSession,
    options: InitOptions,
    *,
    runtime_source: str,
    onboarding_mode: bool,
) -> tuple[object, _InitCollectionOutcome]:
    quickstart = options.setup_mode == SETUP_MODE_QUICKSTART

    provider_result = collect_provider_section(
        session,
        ProviderEditOptions(
            realtime_provider=options.realtime_provider,
            with_vision=options.with_vision,
            without_vision=options.without_vision,
            vision_provider=options.vision_provider,
            with_tooling=options.with_tooling,
            without_tooling=options.without_tooling,
            search_provider=options.search_provider,
            realtime_api_key=options.realtime_api_key,
            vision_api_key=options.vision_api_key,
            search_api_key=options.search_api_key,
        ),
        quickstart=quickstart,
    )
    project_config, env_updates = apply_provider_section(
        session.project_config,
        provider_result,
    )

    security_result = collect_security_section(
        _session_with_project_config(session, project_config),
        SecurityEditOptions(
            backend_profile=options.backend_profile,
            bearer_token=options.bearer_token,
            generate_bearer_token=options.generate_bearer_token,
            clear_bearer_token=options.clear_bearer_token,
        ),
        quickstart=quickstart,
    )
    project_config, security_env_updates = apply_security_section(
        project_config,
        security_result,
    )
    env_updates.update(security_env_updates)

    if onboarding_mode:
        cloud_result = _cloud_result_for_onboarding(
            session=session,
            options=options,
            runtime_source=runtime_source,
        )
    else:
        from portworld_cli.services.config.sections import apply_cloud_section, collect_cloud_section
        from portworld_cli.services.config.types import CloudEditOptions

        cloud_result = collect_cloud_section(
            _session_with_project_config(session, project_config),
            CloudEditOptions(
                project_mode=options.project_mode,
                runtime_source=runtime_source,
                cloud_provider=options.cloud_provider,
                target=options.target,
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
                aws_region=options.aws_region,
                aws_service=options.aws_service,
                aws_vpc_id=options.aws_vpc_id,
                aws_subnet_ids=options.aws_subnet_ids,
                azure_subscription=options.azure_subscription,
                azure_resource_group=options.azure_resource_group,
                azure_region=options.azure_region,
                azure_environment=options.azure_environment,
                azure_app=options.azure_app,
            ),
            prompt_defaults_when_local=False,
            quickstart=quickstart,
        )
        project_config, cloud_env_updates = apply_cloud_section(project_config, cloud_result)
        env_updates.update(cloud_env_updates)
        preview_outcome = preview_secret_readiness(session, project_config, env_updates)
        return project_config, _InitCollectionOutcome(
            env_updates=env_updates,
            review_lines=build_init_confirmation_lines(
                project_config=project_config,
                secret_readiness=preview_outcome,
            ),
        )

    from portworld_cli.services.config.sections import apply_cloud_section

    project_config, cloud_env_updates = apply_cloud_section(project_config, cloud_result)
    env_updates.update(cloud_env_updates)
    preview_outcome = preview_secret_readiness(session, project_config, env_updates)
    return project_config, _InitCollectionOutcome(
        env_updates=env_updates,
        review_lines=build_init_confirmation_lines(
            project_config=project_config,
            secret_readiness=preview_outcome,
        ),
    )


def _cloud_result_for_onboarding(
    *,
    session: ConfigSession,
    options: InitOptions,
    runtime_source: str,
) -> CloudSectionResult:
    return CloudSectionResult(
        project_mode=options.project_mode or PROJECT_MODE_LOCAL,
        runtime_source=runtime_source,
        cloud_provider=options.cloud_provider,
        preferred_target=options.target,
        gcp_cloud_run=session.project_config.deploy.gcp_cloud_run,
        aws_ecs_fargate=session.project_config.deploy.aws_ecs_fargate,
        azure_container_apps=session.project_config.deploy.azure_container_apps,
    )


def _collect_interactive_init_decision(
    cli_context: CLIContext,
    options: InitOptions,
) -> _InteractiveInitDecision:
    current_session: ConfigSession | None
    try:
        current_session = load_workspace_session(cli_context)
    except Exception:
        current_session = None

    click.echo("PortWorld sets up a voice-and-vision assistant backend, its runtime providers, and the connection details your app needs.")
    click.echo("This wizard will configure the backend, generate missing auth, and then run the selected local or cloud path for you.")

    setup_mode = _resolve_setup_mode(cli_context, options)
    if options.setup_mode is None:
        setup_mode = prompt_choice(
            cli_context,
            message="Setup mode",
            choices=(SETUP_MODE_QUICKSTART, SETUP_MODE_MANUAL),
            default=SETUP_MODE_QUICKSTART,
            labels={
                SETUP_MODE_QUICKSTART: "Quickstart (recommended)",
                SETUP_MODE_MANUAL: "Manual / advanced",
            },
        )

    requested_target = _resolve_requested_target(options)
    if requested_target is None:
        requested_target = prompt_choice(
            cli_context,
            message="Where do you want to run PortWorld?",
            choices=(LOCAL_TARGET, CLOUD_PROVIDER_GCP, CLOUD_PROVIDER_AWS, CLOUD_PROVIDER_AZURE),
            default=LOCAL_TARGET,
            labels={
                LOCAL_TARGET: "Local",
                CLOUD_PROVIDER_GCP: "GCP Cloud Run",
                CLOUD_PROVIDER_AWS: "AWS ECS/Fargate",
                CLOUD_PROVIDER_AZURE: "Azure Container Apps",
            },
        )

    published_target: PublishedWorkspaceTarget | None = None
    has_source_checkout = current_session is not None and current_session.project_paths is not None
    runtime_source = _resolve_runtime_source_for_target(
        cli_context,
        options=options,
        setup_mode=setup_mode,
        requested_target=requested_target,
        has_source_checkout=has_source_checkout,
    )
    prompt_session = current_session
    if prompt_session is None and runtime_source == RUNTIME_SOURCE_PUBLISHED:
        published_target = _resolve_published_target(cli_context, options, existing_target=None)
        prompt_session = _build_published_prompt_session(
            cli_context,
            workspace_root=published_target.workspace_root,
        )
    elif runtime_source == RUNTIME_SOURCE_PUBLISHED and current_session is not None and current_session.project_paths is None:
        published_target = PublishedWorkspaceTarget(
            workspace_root=current_session.workspace_root,
            stack_name=current_session.workspace_root.name,
        )

    if runtime_source == RUNTIME_SOURCE_SOURCE and current_session is None:
        raise ProjectRootResolutionError(
            "Source runtime setup requires a PortWorld source checkout. Run `portworld init` from the repo root or use the default published local runtime."
        )

    prompt_session = prompt_session or current_session
    assert prompt_session is not None

    resolved_options = replace(
        options,
        setup_mode=setup_mode,
        runtime_source=runtime_source,
        local_runtime=runtime_source if requested_target == LOCAL_TARGET else options.local_runtime,
        project_mode=(PROJECT_MODE_LOCAL if requested_target == LOCAL_TARGET else PROJECT_MODE_MANAGED),
        cloud_provider=(
            None
            if requested_target == LOCAL_TARGET
            else requested_target
        ),
        target=_target_for_selection(requested_target),
    )
    resolved_options = _prompt_onboarding_inputs(
        prompt_session,
        resolved_options,
    )
    return _InteractiveInitDecision(
        options=resolved_options,
        published_target=published_target,
        prompt_session=prompt_session,
    )


def _prompt_onboarding_inputs(
    session: ConfigSession,
    options: InitOptions,
) -> InitOptions:
    setup_mode = options.setup_mode or SETUP_MODE_QUICKSTART
    quickstart = setup_mode == SETUP_MODE_QUICKSTART
    labels = {
        provider_id: get_provider_requirement(kind=PROVIDER_KIND_REALTIME, provider_id=provider_id).display_name
        for provider_id in ("openai", "gemini_live")
    }
    realtime_provider = options.realtime_provider or prompt_choice(
        session.cli_context,
        message="Realtime provider",
        choices=("openai", "gemini_live"),
        default=_default_provider_value(session.project_config.providers.realtime.provider, "openai"),
        labels=labels,
    )
    click.echo(f"Configuring {labels[realtime_provider]}.")
    realtime_updates = _prompt_provider_env_values(
        session,
        kind=PROVIDER_KIND_REALTIME,
        provider_id=realtime_provider,
        explicit_secret_value=options.realtime_api_key,
    )

    vision_enabled = _resolve_bool_flag(
        enable_flag=options.with_vision,
        disable_flag=options.without_vision,
        default=False,
    )
    if not options.with_vision and not options.without_vision:
        vision_enabled = prompt_confirm(
            session.cli_context,
            message="Enable vision memory?",
            default=session.project_config.providers.vision.enabled if not quickstart else False,
        )

    vision_provider = options.vision_provider
    vision_updates: dict[str, str] = {}
    tooling_enabled = _resolve_bool_flag(
        enable_flag=options.with_tooling,
        disable_flag=options.without_tooling,
        default=vision_enabled,
    )
    search_provider: str | None = None
    search_api_key: str | None = None
    if vision_enabled:
        vision_choices = (
            "mistral",
            "nvidia_integrate",
            "openai",
            "azure_openai",
            "gemini",
            "claude",
            "bedrock",
            "groq",
        )
        vision_provider = vision_provider or prompt_choice(
            session.cli_context,
            message="Vision provider",
            choices=vision_choices,
            default=_default_provider_value(session.project_config.providers.vision.provider, "mistral"),
            labels={
                provider_id: get_provider_requirement(
                    kind=PROVIDER_KIND_VISION,
                    provider_id=provider_id,
                ).display_name
                for provider_id in vision_choices
            },
        )
        click.echo(f"Vision memory will use {get_provider_requirement(kind=PROVIDER_KIND_VISION, provider_id=vision_provider).display_name}.")
        vision_updates = _prompt_provider_env_values(
            session,
            kind=PROVIDER_KIND_VISION,
            provider_id=vision_provider,
            explicit_secret_value=options.vision_api_key,
        )
        click.echo("Vision memory and realtime tooling will be enabled by default because a vision provider was selected.")
        setup_tavily = prompt_confirm(
            session.cli_context,
            message="Set up Tavily web search for realtime tooling now?",
            default=True,
        )
        if setup_tavily:
            search_provider = "tavily"
            search_updates = _prompt_provider_env_values(
                session,
                kind=PROVIDER_KIND_SEARCH,
                provider_id=search_provider,
                explicit_secret_value=options.search_api_key,
            )
            search_api_key = search_updates.get("TAVILY_API_KEY")
            tooling_enabled = True
        else:
            tooling_enabled = False
    elif tooling_enabled:
        setup_tavily = prompt_confirm(
            session.cli_context,
            message="Enable realtime tooling with Tavily web search?",
            default=False,
        )
        if setup_tavily:
            search_provider = "tavily"
            search_updates = _prompt_provider_env_values(
                session,
                kind=PROVIDER_KIND_SEARCH,
                provider_id=search_provider,
                explicit_secret_value=options.search_api_key,
            )
            search_api_key = search_updates.get("TAVILY_API_KEY")
            tooling_enabled = True
        else:
            tooling_enabled = False

    existing_bearer = _existing_env_value(session, "BACKEND_BEARER_TOKEN")
    backend_profile = options.backend_profile or (
        "production" if options.project_mode == PROJECT_MODE_MANAGED else "development"
    )
    generate_bearer = options.generate_bearer_token
    if (
        options.bearer_token is None
        and not options.clear_bearer_token
        and not options.generate_bearer_token
        and not existing_bearer.strip()
    ):
        generate_bearer = True
        click.echo("A backend bearer token will be generated automatically.")
    elif existing_bearer.strip():
        click.echo("Keeping the existing backend bearer token.")

    return replace(
        options,
        setup_mode=setup_mode,
        realtime_provider=realtime_provider,
        realtime_api_key=realtime_updates.get(_secret_key_for_kind(PROVIDER_KIND_REALTIME, realtime_provider)),
        with_vision=vision_enabled,
        without_vision=not vision_enabled,
        vision_provider=vision_provider if vision_enabled else None,
        vision_api_key=(
            vision_updates.get(_secret_key_for_kind(PROVIDER_KIND_VISION, vision_provider))
            if vision_enabled and vision_provider is not None
            else None
        ),
        with_tooling=tooling_enabled,
        without_tooling=not tooling_enabled,
        search_provider=search_provider,
        search_api_key=search_api_key,
        backend_profile=backend_profile,
        generate_bearer_token=generate_bearer,
    )


def _prompt_provider_env_values(
    session: ConfigSession,
    *,
    kind: str,
    provider_id: str,
    explicit_secret_value: str | None,
) -> dict[str, str]:
    requirement = get_provider_requirement(kind=kind, provider_id=provider_id)
    updates: dict[str, str] = {}
    for env_key in requirement.required_secret_env_keys:
        updates[env_key] = resolve_secret_value(
            session.cli_context,
            label=f"{env_key} ({requirement.display_name})",
            existing_value=_existing_env_value(session, env_key),
            explicit_value=explicit_secret_value if len(requirement.required_secret_env_keys) == 1 else None,
            required=True,
            prompt_when_existing=True,
        )
    for env_key in requirement.required_non_secret_env_keys:
        updates[env_key] = resolve_required_text_value(
            session.cli_context,
            prompt=f"{env_key} ({requirement.display_name})",
            current_value=_existing_env_value(session, env_key),
            explicit_value=None,
            prompt_when_current_set=True,
        )
    return updates


def _execute_post_init(
    cli_context: CLIContext,
    *,
    options: InitOptions,
    session: ConfigSession,
    env_updates: dict[str, str],
) -> CommandResult:
    if session.project_config.project_mode == PROJECT_MODE_LOCAL:
        execution = (
            _start_published_runtime(session)
            if session.effective_runtime_source == RUNTIME_SOURCE_PUBLISHED
            else _start_source_runtime(session)
        )
    else:
        execution = _run_managed_deploy(cli_context, options)

    if not execution.ok:
        return execution

    service_url = str(execution.data.get("backend_url") or execution.data.get("service_url") or "")
    _emit_progress(cli_context, "Syncing repo-local iOS defaults.")
    ios_sync = _sync_repo_ios_defaults(
        cli_context=cli_context,
        session=session,
        backend_url=service_url,
        bearer_token=env_updates.get("BACKEND_BEARER_TOKEN", ""),
    )
    if not ios_sync.ok:
        return ios_sync

    merged_data = dict(execution.data)
    if ios_sync.data:
        merged_data["_summary_lines"] = _execution_summary_lines(execution.data) + _execution_summary_lines(ios_sync.data)
        merged_data.update(_public_execution_data(ios_sync.data))
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        message=None,
        data=merged_data,
        exit_code=0,
        checks=(),
    )


def _start_source_runtime(session: ConfigSession) -> CommandResult:
    assert session.project_paths is not None
    _emit_progress(session.cli_context, "Starting the local backend from the source checkout. This can take a while on the first build.")
    command = ["docker", "compose", "up", "--build", "-d"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=session.project_paths.project_root,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip() or "docker compose up --build -d failed."
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=f"Configuration was written, but local startup failed.\nproblem: {message}",
            data={"status": "error", "error_type": "LocalRuntimeStartError"},
            exit_code=1,
        )
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        data={
            "backend_url": "http://127.0.0.1:8080",
            "execution_mode": "local_source",
            "execution_command": "docker compose up --build -d",
            "_summary_lines": (
                "local_runtime: source",
                "backend_url: http://127.0.0.1:8080",
                "runtime_command: docker compose up --build -d",
            ),
        },
        exit_code=0,
    )


def _start_published_runtime(session: ConfigSession) -> CommandResult:
    workspace_root = session.workspace_root
    _emit_progress(session.cli_context, "Starting the published local backend. Docker may need a moment to pull the image.")
    command = build_compose_command(workspace_root, "up", "-d")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=workspace_root,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip() or "docker compose up -d failed."
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=f"Configuration was written, but local startup failed.\nproblem: {message}",
            data={"status": "error", "error_type": "LocalRuntimeStartError"},
            exit_code=1,
        )
    host_port = session.project_config.deploy.published_runtime.host_port
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        data={
            "backend_url": f"http://127.0.0.1:{host_port}",
            "execution_mode": "local_published",
            "execution_command": "docker compose up -d",
            "_summary_lines": (
                "local_runtime: published",
                f"backend_url: http://127.0.0.1:{host_port}",
                "runtime_command: docker compose up -d",
            ),
        },
        exit_code=0,
    )


def _run_managed_deploy(cli_context: CLIContext, options: InitOptions) -> CommandResult:
    execution_context = replace(cli_context, yes=True)
    _emit_progress(cli_context, f"Starting managed deploy for {options.target}.")
    if options.target == TARGET_GCP_CLOUD_RUN:
        result = run_deploy_gcp_cloud_run(
            execution_context,
            DeployGCPCloudRunOptions(
                project=options.project,
                region=options.region,
                service=options.service,
                artifact_repo=options.artifact_repo,
                sql_instance=options.sql_instance,
                database=options.database,
                bucket=options.bucket,
                tag=None,
                min_instances=options.min_instances,
                max_instances=options.max_instances,
                concurrency=options.concurrency,
                cpu=options.cpu,
                memory=options.memory,
            ),
        )
    elif options.target == TARGET_AWS_ECS_FARGATE:
        result = run_deploy_aws_ecs_fargate(
            execution_context,
            DeployAWSECSFargateOptions(
                region=options.aws_region,
                service=options.aws_service,
                vpc_id=options.aws_vpc_id,
                subnet_ids=options.aws_subnet_ids,
                database_url=None,
                bucket=options.bucket,
                ecr_repo=None,
                tag=None,
            ),
        )
    elif options.target == TARGET_AZURE_CONTAINER_APPS:
        result = run_deploy_azure_container_apps(
            execution_context,
            DeployAzureContainerAppsOptions(
                subscription=options.azure_subscription,
                resource_group=options.azure_resource_group,
                region=options.azure_region,
                environment=options.azure_environment,
                app=options.azure_app,
                database_url=None,
                storage_account=None,
                blob_container=None,
                blob_endpoint=None,
                acr_server=None,
                acr_repo=None,
                tag=None,
            ),
        )
    else:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message="Managed init could not determine a deploy target.",
            data={"status": "error", "error_type": "DeployTargetResolutionError"},
            exit_code=1,
        )

    if not result.ok:
        return CommandResult(
            ok=False,
            command=COMMAND_NAME,
            message=f"Configuration was written, but the managed deploy failed.\n{result.message or ''}".strip(),
            data=result.data,
            checks=result.checks,
            exit_code=result.exit_code,
        )

    service_url = str(result.data.get("service_url") or "")
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        data={
            "service_url": service_url,
            "backend_url": service_url,
            "execution_mode": "managed_deploy",
            "deploy_target": options.target,
            "_summary_lines": (
                f"deploy_target: {options.target}",
                f"backend_url: {service_url}",
            ),
        },
        exit_code=0,
    )


def _sync_repo_ios_defaults(
    *,
    cli_context: CLIContext,
    session: ConfigSession,
    backend_url: str,
    bearer_token: str,
) -> CommandResult:
    project_root = None if session.project_paths is None else session.project_paths.project_root
    if project_root is None:
        try:
            project_root = resolve_project_paths(
                explicit_root=cli_context.project_root_override,
                start=Path.cwd(),
            ).project_root
        except ProjectRootResolutionError:
            return CommandResult(ok=True, command=COMMAND_NAME, data={}, exit_code=0)

    ios_config_dir = project_root / "IOS" / "Config"
    if not ios_config_dir.is_dir():
        return CommandResult(ok=True, command=COMMAND_NAME, data={}, exit_code=0)

    template_path = ios_config_dir / "Config.xcconfig.template"
    debug_path = ios_config_dir / "Debug.xcconfig"
    release_path = ios_config_dir / "Release.xcconfig"
    template_text = template_path.read_text(encoding="utf-8") if template_path.is_file() else ""
    _write_xcconfig_file(
        path=debug_path,
        template_text=template_text or "// Local development configuration\n",
        backend_url=backend_url,
        bearer_token=bearer_token,
        include_bearer_token=True,
    )
    _write_xcconfig_file(
        path=release_path,
        template_text=template_text or "// Release configuration\n",
        backend_url=backend_url,
        bearer_token=bearer_token,
        include_bearer_token=True,
    )
    return CommandResult(
        ok=True,
        command=COMMAND_NAME,
        data={
            "ios_backend_config_synced": True,
            "ios_debug_xcconfig": str(debug_path),
            "ios_release_xcconfig": str(release_path),
            "_summary_lines": (
                "ios_config_sync: yes",
                f"ios_debug_xcconfig: {debug_path}",
                f"ios_release_xcconfig: {release_path}",
            ),
        },
        exit_code=0,
    )


def _write_xcconfig_file(
    *,
    path: Path,
    template_text: str,
    backend_url: str,
    bearer_token: str,
    include_bearer_token: bool,
) -> None:
    content = path.read_text(encoding="utf-8") if path.is_file() else template_text
    content = _upsert_xcconfig_value(content, "BACKEND_BASE_URL", backend_url)
    if include_bearer_token:
        content = _upsert_xcconfig_value(content, "BACKEND_BEARER_TOKEN", bearer_token)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _upsert_xcconfig_value(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    replacement = f"{key} = {value}"
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key} ="):
            lines[index] = replacement
            return "\n".join(lines)
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(replacement)
    return "\n".join(lines)


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


def _build_published_prompt_session(
    cli_context: CLIContext,
    *,
    workspace_root: Path,
) -> ConfigSession:
    return _build_published_init_session(
        cli_context,
        workspace_paths=WorkspacePaths.from_root(workspace_root),
    )


def _resolve_requested_target(options: InitOptions) -> str | None:
    if options.target in {TARGET_GCP_CLOUD_RUN, TARGET_AWS_ECS_FARGATE, TARGET_AZURE_CONTAINER_APPS}:
        return {
            TARGET_GCP_CLOUD_RUN: CLOUD_PROVIDER_GCP,
            TARGET_AWS_ECS_FARGATE: CLOUD_PROVIDER_AWS,
            TARGET_AZURE_CONTAINER_APPS: CLOUD_PROVIDER_AZURE,
        }[str(options.target)]
    if options.cloud_provider in {CLOUD_PROVIDER_GCP, CLOUD_PROVIDER_AWS, CLOUD_PROVIDER_AZURE}:
        return str(options.cloud_provider)
    if options.project_mode == PROJECT_MODE_LOCAL:
        return LOCAL_TARGET
    if options.runtime_source in {RUNTIME_SOURCE_PUBLISHED, RUNTIME_SOURCE_SOURCE}:
        return LOCAL_TARGET
    return None


def _resolve_runtime_source_for_target(
    cli_context: CLIContext,
    *,
    options: InitOptions,
    setup_mode: str,
    requested_target: str,
    has_source_checkout: bool,
) -> str:
    if options.local_runtime in {RUNTIME_SOURCE_PUBLISHED, RUNTIME_SOURCE_SOURCE}:
        return str(options.local_runtime)
    if options.runtime_source in {RUNTIME_SOURCE_PUBLISHED, RUNTIME_SOURCE_SOURCE}:
        return str(options.runtime_source)
    if requested_target != LOCAL_TARGET:
        return RUNTIME_SOURCE_PUBLISHED
    if setup_mode == SETUP_MODE_MANUAL and has_source_checkout:
        return prompt_choice(
            cli_context,
            message="Local runtime",
            choices=(RUNTIME_SOURCE_PUBLISHED, RUNTIME_SOURCE_SOURCE),
            default=RUNTIME_SOURCE_PUBLISHED,
            labels={
                RUNTIME_SOURCE_PUBLISHED: "Published GHCR image (recommended)",
                RUNTIME_SOURCE_SOURCE: "Build from local source checkout",
            },
        )
    return RUNTIME_SOURCE_PUBLISHED


def _target_for_selection(selection: str) -> str | None:
    return {
        LOCAL_TARGET: None,
        CLOUD_PROVIDER_GCP: TARGET_GCP_CLOUD_RUN,
        CLOUD_PROVIDER_AWS: TARGET_AWS_ECS_FARGATE,
        CLOUD_PROVIDER_AZURE: TARGET_AZURE_CONTAINER_APPS,
    }[selection]


def _resolve_setup_mode(cli_context: CLIContext, options: InitOptions) -> str:
    if options.setup_mode in {SETUP_MODE_QUICKSTART, SETUP_MODE_MANUAL}:
        return str(options.setup_mode)
    if cli_context.non_interactive:
        return SETUP_MODE_MANUAL
    return SETUP_MODE_QUICKSTART


def _select_first_run_runtime_source(cli_context: CLIContext) -> str:
    if cli_context.non_interactive:
        return RUNTIME_SOURCE_PUBLISHED

    click.echo("How do you want to set up PortWorld?")
    click.echo("  operator: zero-clone workspace with published runtime images (recommended)")
    click.echo("  contributor: repo-backed source checkout workflow")
    selection = prompt_choice(
        cli_context,
        message="Setup flow",
        choices=("operator", "contributor"),
        default="operator",
        labels={
            "operator": "Operator (published runtime, recommended)",
            "contributor": "Contributor (source checkout workflow)",
        },
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
            "Source init requires a PortWorld checkout. "
            "Run this command from the repo root, or rerun `portworld init` and keep the default published local runtime."
        ),
        data={
            "status": "error",
            "error_type": ProjectRootResolutionError.__name__,
        },
        checks=(
            DiagnosticCheck(
                id="project-root",
                status="fail",
                message="No PortWorld source checkout was detected for source runtime setup.",
                action="Clone the repo and run `portworld init` there, or use the default published local runtime.",
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


def _planned_execution_lines(project_config) -> tuple[str, ...]:
    if project_config.project_mode == PROJECT_MODE_LOCAL:
        if project_config.runtime_source == RUNTIME_SOURCE_SOURCE:
            return ("execution_plan: start the local source runtime with docker compose",)
        return ("execution_plan: start the published local runtime with docker compose",)
    return (f"execution_plan: deploy to {project_config.deploy.preferred_target or 'managed target'}",)


def _build_onboarding_confirmation_lines(
    *,
    project_config,
    workspace_root: Path | None = None,
    stack_name: str | None = None,
) -> tuple[str, ...]:
    lines = [
        "Review setup before continuing:",
        (
            f"- Run locally with the {'published GHCR image' if project_config.runtime_source == RUNTIME_SOURCE_PUBLISHED else 'local source build'}"
            if project_config.project_mode == PROJECT_MODE_LOCAL
            else f"- Deploy to {project_config.deploy.preferred_target}"
        ),
        f"- Realtime provider: {get_provider_requirement(kind=PROVIDER_KIND_REALTIME, provider_id=project_config.providers.realtime.provider).display_name}",
        (
            f"- Vision memory: enabled ({get_provider_requirement(kind=PROVIDER_KIND_VISION, provider_id=project_config.providers.vision.provider).display_name})"
            if project_config.providers.vision.enabled
            else "- Vision memory: disabled"
        ),
        (
            f"- Realtime tooling: enabled ({get_provider_requirement(kind=PROVIDER_KIND_SEARCH, provider_id=project_config.providers.tooling.web_search_provider).display_name})"
            if project_config.providers.tooling.enabled
            else "- Realtime tooling: disabled"
        ),
        "- Bearer token: will be configured automatically",
    ]
    if workspace_root is not None:
        lines.append(f"- Workspace: {workspace_root}")
    if stack_name is not None:
        lines.append(f"- Stack: {stack_name}")
    return tuple(lines)


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


def _build_final_success_message(
    *,
    project_config,
    secret_readiness,
    project_config_path: Path,
    env_path: Path | None,
    backup_path: Path | None,
    extra_lines: tuple[str | None, ...],
) -> str:
    lines = list(
        build_init_confirmation_lines(
            project_config=project_config,
            secret_readiness=secret_readiness,
        )
    )
    lines.append(f"project_config_path: {project_config_path}")
    if env_path is not None:
        lines.append(f"env_path: {env_path}")
    if backup_path is not None:
        lines.append(f"backup_path: {backup_path}")
    lines.extend(line for line in extra_lines if line)
    if project_config.project_mode == PROJECT_MODE_LOCAL:
        lines.extend(
            (
                "next: portworld doctor --target local",
                "next: portworld status",
                "next: portworld config show",
            )
        )
    else:
        lines.extend(
            (
                f"next: portworld doctor --target {project_config.deploy.preferred_target}",
                "next: portworld status",
                "next: portworld config show",
            )
        )
    return "\n".join(lines)


def _default_provider_value(current_value: str | None, fallback: str) -> str:
    value = (current_value or "").strip().lower()
    return value or fallback


def _existing_env_value(session: ConfigSession, env_key: str) -> str:
    if session.existing_env is None:
        return ""
    value = session.existing_env.known_values.get(env_key)
    if value is None:
        value = session.existing_env.preserved_overrides.get(env_key)
    return str(value or "")


def _resolve_bool_flag(*, enable_flag: bool, disable_flag: bool, default: bool) -> bool:
    if enable_flag:
        return True
    if disable_flag:
        return False
    return default


def _secret_key_for_kind(kind: str, provider_id: str | None) -> str | None:
    if provider_id is None:
        return None
    requirement = get_provider_requirement(kind=kind, provider_id=provider_id)
    if len(requirement.required_secret_env_keys) != 1:
        return None
    return requirement.required_secret_env_keys[0]


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


def _execution_summary_lines(data: dict[str, object]) -> tuple[str, ...]:
    raw = data.get("_summary_lines")
    if not isinstance(raw, tuple):
        return ()
    return tuple(str(line) for line in raw)


def _public_execution_data(data: dict[str, object]) -> dict[str, object]:
    public = dict(data)
    public.pop("_summary_lines", None)
    return public


def _emit_progress(cli_context: CLIContext, message: str) -> None:
    if cli_context.json_output:
        return
    click.echo(f"... {message}")

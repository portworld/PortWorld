from __future__ import annotations

from dataclasses import replace
import unittest

from portworld_cli.context import CLIContext
from portworld_cli.services.config.prompts import (
    resolve_choice_value,
    resolve_required_text_value,
    resolve_secret_value,
)
from portworld_cli.services.init.service import (
    InitOptions,
    LOCAL_TARGET,
    RUNTIME_SOURCE_PUBLISHED,
    RUNTIME_SOURCE_SOURCE,
    SETUP_MODE_MANUAL,
    SETUP_MODE_QUICKSTART,
    _resolve_setup_mode,
    _resolve_runtime_source_for_target,
)


def _base_cli_context(*, non_interactive: bool) -> CLIContext:
    return CLIContext(
        project_root_override=None,
        verbose=False,
        json_output=False,
        non_interactive=non_interactive,
        yes=False,
    )


def _base_init_options() -> InitOptions:
    return InitOptions(
        force=False,
        realtime_provider=None,
        with_vision=False,
        without_vision=False,
        vision_provider=None,
        with_tooling=False,
        without_tooling=False,
        search_provider=None,
        realtime_api_key=None,
        vision_api_key=None,
        search_api_key=None,
        backend_profile=None,
        bearer_token=None,
        generate_bearer_token=False,
        clear_bearer_token=False,
        setup_mode=None,
        project_mode=None,
        runtime_source=None,
        local_runtime=None,
        cloud_provider=None,
        target=None,
        stack_name=None,
        release_tag=None,
        host_port=None,
        project=None,
        region=None,
        service=None,
        artifact_repo=None,
        sql_instance=None,
        database=None,
        bucket=None,
        min_instances=None,
        max_instances=None,
        concurrency=None,
        cpu=None,
        memory=None,
        aws_region=None,
        aws_service=None,
        aws_vpc_id=None,
        aws_subnet_ids=None,
        azure_subscription=None,
        azure_resource_group=None,
        azure_region=None,
        azure_environment=None,
        azure_app=None,
    )


class PromptDefaultBehaviorTests(unittest.TestCase):
    def test_choice_can_skip_prompt_when_unspecified(self) -> None:
        value = resolve_choice_value(
            _base_cli_context(non_interactive=False),
            prompt="Project mode",
            current_value="local",
            explicit_value=None,
            choices=("local", "managed"),
            prompt_when_unspecified=False,
        )
        self.assertEqual(value, "local")

    def test_required_text_can_keep_existing_without_prompt(self) -> None:
        value = resolve_required_text_value(
            _base_cli_context(non_interactive=False),
            prompt="Cloud Run service name",
            current_value="portworld-backend",
            explicit_value=None,
            prompt_when_current_set=False,
        )
        self.assertEqual(value, "portworld-backend")

    def test_secret_can_keep_existing_without_prompt(self) -> None:
        value = resolve_secret_value(
            _base_cli_context(non_interactive=False),
            label="OPENAI_API_KEY (OpenAI Realtime)",
            existing_value="existing-key",
            explicit_value=None,
            required=True,
            prompt_when_existing=False,
        )
        self.assertEqual(value, "existing-key")

    def test_setup_mode_defaults_to_manual_when_non_interactive(self) -> None:
        mode = _resolve_setup_mode(
            _base_cli_context(non_interactive=True),
            _base_init_options(),
        )
        self.assertEqual(mode, SETUP_MODE_MANUAL)

    def test_setup_mode_defaults_to_quickstart_when_interactive(self) -> None:
        mode = _resolve_setup_mode(
            _base_cli_context(non_interactive=False),
            _base_init_options(),
        )
        self.assertEqual(mode, SETUP_MODE_QUICKSTART)

    def test_setup_mode_keeps_explicit_value(self) -> None:
        options = replace(_base_init_options(), setup_mode=SETUP_MODE_QUICKSTART)
        mode = _resolve_setup_mode(
            _base_cli_context(non_interactive=True),
            options,
        )
        self.assertEqual(mode, SETUP_MODE_QUICKSTART)

    def test_repo_quickstart_defaults_local_runtime_to_source(self) -> None:
        runtime_source = _resolve_runtime_source_for_target(
            _base_cli_context(non_interactive=False),
            options=_base_init_options(),
            setup_mode=SETUP_MODE_QUICKSTART,
            requested_target=LOCAL_TARGET,
            has_source_checkout=True,
        )
        self.assertEqual(runtime_source, RUNTIME_SOURCE_SOURCE)

    def test_non_repo_quickstart_keeps_local_runtime_published(self) -> None:
        runtime_source = _resolve_runtime_source_for_target(
            _base_cli_context(non_interactive=False),
            options=_base_init_options(),
            setup_mode=SETUP_MODE_QUICKSTART,
            requested_target=LOCAL_TARGET,
            has_source_checkout=False,
        )
        self.assertEqual(runtime_source, RUNTIME_SOURCE_PUBLISHED)


if __name__ == "__main__":
    unittest.main()

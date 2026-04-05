from __future__ import annotations

import unittest
from pathlib import Path

from portworld_cli.providers.service import run_providers_list
from portworld_cli.services.config.messages import build_config_show_message
from portworld_cli.context import CLIContext
from portworld_cli.workspace.project_config import ProjectConfig, ProvidersConfig, RealtimeProviderConfig, ToolingConfig, VisionProviderConfig
from portworld_cli.workspace.session import SecretReadiness


def _cli_context() -> CLIContext:
    return CLIContext(
        project_root_override=None,
        verbose=False,
        json_output=False,
        non_interactive=True,
        yes=False,
    )


def _secret_readiness() -> SecretReadiness:
    return SecretReadiness(
        selected_realtime_provider="gemini_live",
        selected_vision_provider="gemini",
        selected_search_provider="tavily",
        required_secret_keys=("GEMINI_LIVE_API_KEY", "VISION_GEMINI_API_KEY", "TAVILY_API_KEY"),
        optional_secret_keys=(),
        missing_required_secret_keys=(),
        required_config_keys=(),
        optional_config_keys=(),
        missing_required_config_keys=(),
        key_presence={
            "GEMINI_LIVE_API_KEY": True,
            "VISION_GEMINI_API_KEY": True,
            "TAVILY_API_KEY": True,
        },
        config_key_presence={},
        bearer_token_present=True,
    )


class HumanOutputTests(unittest.TestCase):
    def test_config_show_uses_human_summary_language(self) -> None:
        message = build_config_show_message(
            workspace_root=Path("/tmp/portworld"),
            project_config=ProjectConfig(
                project_mode="managed",
                runtime_source="source",
                cloud_provider="gcp",
                providers=ProvidersConfig(
                    realtime=RealtimeProviderConfig(provider="gemini_live"),
                    vision=VisionProviderConfig(enabled=True, provider="gemini"),
                    tooling=ToolingConfig(enabled=True, web_search_provider="tavily"),
                ),
            ),
            secret_readiness=_secret_readiness(),
            project_root=None,
            env_path=None,
            derived_from_legacy=False,
            configured_runtime_source="source",
            effective_runtime_source="source",
            runtime_source_derived_from_legacy=False,
            workspace_resolution_source="cwd",
            active_workspace_root=None,
        )

        self.assertIn("realtime", message)
        self.assertIn("Gemini Live", message)
        self.assertIn("enabled (Gemini Vision)", message)
        self.assertIn("enabled (Tavily Search)", message)
        self.assertIn("all required credentials present", message)
        self.assertNotIn("workspace_root", message)
        self.assertNotIn("required_provider_secrets", message)

    def test_providers_list_leads_with_display_names(self) -> None:
        result = run_providers_list(_cli_context())
        message = result.message or ""

        self.assertIn("- GCP Cloud Run [Default]", message)
        self.assertIn("id: gcp", message)
        self.assertIn("- OpenAI Realtime [Default]", message)
        self.assertIn("id: openai", message)
        self.assertNotIn("(default: yes)", message)


if __name__ == "__main__":
    unittest.main()

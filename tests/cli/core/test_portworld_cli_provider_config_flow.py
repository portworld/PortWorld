from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from portworld_cli.context import CLIContext
from portworld_cli.envfile import load_env_template, parse_env_file
from portworld_cli.providers.types import ProviderEditOptions
from portworld_cli.services.config.errors import ConfigUsageError, ConfigValidationError
from portworld_cli.services.config.messages import build_init_review_lines
from portworld_cli.services.config.persistence import write_config_artifacts
from portworld_cli.workspace.config.providers import collect_provider_section
from portworld_cli.workspace.discovery.paths import ProjectPaths, WorkspacePaths
from portworld_cli.workspace.project_config import ProjectConfig
from portworld_cli.workspace.session import SecretReadiness, WorkspaceSession


class ProviderConfigFlowTests(unittest.TestCase):
    def _build_session(
        self,
        workspace_root: Path,
        *,
        env_text: str = "OPENAI_API_KEY=current-openai\n",
        project_config: ProjectConfig | None = None,
    ) -> WorkspaceSession:
        backend_dir = workspace_root / "backend"
        backend_dir.mkdir(parents=True, exist_ok=True)
        (backend_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

        repo_env_example = Path(__file__).resolve().parents[3] / "backend" / ".env.example"
        (backend_dir / ".env.example").write_text(
            repo_env_example.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (backend_dir / ".env").write_text(env_text, encoding="utf-8")
        (workspace_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (workspace_root / ".portworld").mkdir(parents=True, exist_ok=True)

        template = load_env_template(backend_dir / ".env.example")
        existing_env = parse_env_file(backend_dir / ".env", template=template)

        return WorkspaceSession(
            cli_context=CLIContext(
                project_root_override=workspace_root,
                verbose=False,
                json_output=False,
                non_interactive=True,
                yes=True,
            ),
            workspace_paths=WorkspacePaths.from_root(workspace_root),
            project_paths=ProjectPaths.from_root(workspace_root),
            template=template,
            existing_env=existing_env,
            project_config=project_config or ProjectConfig(runtime_source="source"),
            configured_runtime_source="source",
            effective_runtime_source="source",
            remembered_deploy_state={"service_name": "test-service"},
            remembered_deploy_state_target="aws-ecs-fargate",
            workspace_resolution_source="explicit",
            active_workspace_root=workspace_root,
        )

    def test_write_config_artifacts_preserves_required_session_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._build_session(Path(temp_dir))
            outcome = write_config_artifacts(
                session,
                session.project_config,
                {"OPENAI_API_KEY": "updated-openai"},
            )

            self.assertEqual(outcome.secret_readiness.selected_realtime_provider, "openai")
            self.assertEqual(
                session.workspace_paths.project_config_file.read_text(encoding="utf-8").strip()[:1],
                "{",
            )

    def test_non_interactive_azure_openai_requires_endpoint_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._build_session(Path(temp_dir))
            with self.assertRaisesRegex(
                ConfigValidationError,
                "VISION_AZURE_OPENAI_ENDPOINT \\(Azure OpenAI Vision\\) is required in non-interactive mode.",
            ):
                collect_provider_section(
                    session,
                    ProviderEditOptions(
                        realtime_provider="openai",
                        with_vision=True,
                        without_vision=False,
                        vision_provider="azure_openai",
                        with_tooling=False,
                        without_tooling=False,
                        search_provider=None,
                        realtime_api_key="updated-openai",
                        vision_api_key="azure-key",
                        search_api_key=None,
                        openai_api_key=None,
                        vision_provider_api_key=None,
                        tavily_api_key=None,
                    ),
                )

    def test_non_interactive_bedrock_requires_region_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._build_session(
                Path(temp_dir),
                env_text="OPENAI_API_KEY=current-openai\nVISION_BEDROCK_REGION=\n",
            )
            with self.assertRaisesRegex(
                ConfigValidationError,
                "VISION_BEDROCK_REGION \\(Bedrock Vision\\) is required in non-interactive mode.",
            ):
                collect_provider_section(
                    session,
                    ProviderEditOptions(
                        realtime_provider="openai",
                        with_vision=True,
                        without_vision=False,
                        vision_provider="bedrock",
                        with_tooling=False,
                        without_tooling=False,
                        search_provider=None,
                        realtime_api_key="updated-openai",
                        vision_api_key=None,
                        search_api_key=None,
                        openai_api_key=None,
                        vision_provider_api_key=None,
                        tavily_api_key=None,
                    ),
                )

    def test_vision_provider_flag_requires_vision_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._build_session(Path(temp_dir))
            with self.assertRaisesRegex(
                ConfigUsageError,
                "--vision-provider requires visual memory to be enabled.",
            ):
                collect_provider_section(
                    session,
                    ProviderEditOptions(
                        realtime_provider="openai",
                        with_vision=False,
                        without_vision=False,
                        vision_provider="openai",
                        with_tooling=False,
                        without_tooling=False,
                        search_provider=None,
                        realtime_api_key=None,
                        vision_api_key=None,
                        search_api_key=None,
                        openai_api_key=None,
                        vision_provider_api_key=None,
                        tavily_api_key=None,
                    ),
                )

    def test_search_provider_flag_requires_tooling_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session = self._build_session(Path(temp_dir))
            with self.assertRaisesRegex(
                ConfigUsageError,
                "--search-provider requires realtime tooling to be enabled.",
            ):
                collect_provider_section(
                    session,
                    ProviderEditOptions(
                        realtime_provider="openai",
                        with_vision=False,
                        without_vision=False,
                        vision_provider=None,
                        with_tooling=False,
                        without_tooling=False,
                        search_provider="tavily",
                        realtime_api_key=None,
                        vision_api_key=None,
                        search_api_key=None,
                        openai_api_key=None,
                        vision_provider_api_key=None,
                        tavily_api_key=None,
                    ),
                )

    def test_init_review_lines_include_provider_config_readiness(self) -> None:
        readiness = SecretReadiness(
            selected_realtime_provider="openai",
            selected_vision_provider="azure_openai",
            selected_search_provider=None,
            required_secret_keys=("OPENAI_API_KEY", "VISION_AZURE_OPENAI_API_KEY"),
            optional_secret_keys=(),
            missing_required_secret_keys=(),
            required_config_keys=("VISION_AZURE_OPENAI_ENDPOINT",),
            optional_config_keys=("VISION_AZURE_OPENAI_DEPLOYMENT",),
            missing_required_config_keys=("VISION_AZURE_OPENAI_ENDPOINT",),
            key_presence={"OPENAI_API_KEY": True, "VISION_AZURE_OPENAI_API_KEY": True},
            config_key_presence={"VISION_AZURE_OPENAI_ENDPOINT": False},
            bearer_token_present=True,
        )

        lines = build_init_review_lines(
            project_config=ProjectConfig(runtime_source="source"),
            secret_readiness=readiness,
        )

        self.assertIn(
            "required_provider_config: VISION_AZURE_OPENAI_ENDPOINT:missing",
            lines,
        )
        self.assertIn(
            "missing_provider_config: VISION_AZURE_OPENAI_ENDPOINT",
            lines,
        )


if __name__ == "__main__":
    unittest.main()

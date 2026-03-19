from __future__ import annotations

from collections import OrderedDict
import unittest

from portworld_cli.deploy.config import ResolvedDeployConfig
from portworld_cli.deploy.stages.runtime import (
    build_cloud_run_secret_bindings,
    build_runtime_env_vars,
)


class DeployProviderRuntimeTests(unittest.TestCase):
    def _config(self) -> ResolvedDeployConfig:
        return ResolvedDeployConfig(
            runtime_source="source",
            image_source_mode="source_build",
            project_id="project-id",
            region="us-central1",
            service_name="portworld-backend",
            artifact_repository_base="portworld",
            artifact_repository="us-central1-docker.pkg.dev/project-id/portworld",
            sql_instance_name="portworld-pg",
            database_name="portworld",
            bucket_name="portworld-artifacts",
            cors_origins="https://app.example.com",
            allowed_hosts="api.example.com",
            image_tag="test",
            deploy_image_uri="image",
            published_release_tag=None,
            published_image_ref=None,
            min_instances=1,
            max_instances=1,
            concurrency=10,
            cpu="1",
            memory="1Gi",
        )

    def test_cloud_run_secret_bindings_use_provider_secret_map(self) -> None:
        bindings = build_cloud_run_secret_bindings(
            provider_secret_names={
                "OPENAI_API_KEY": "pw-openai",
                "VISION_OPENAI_API_KEY": "pw-vision-openai",
                "TAVILY_API_KEY": "pw-tavily",
            },
            bearer_secret_name="pw-bearer",
            database_url_secret_name="pw-db",
        )
        self.assertEqual(bindings["OPENAI_API_KEY"], "pw-openai:latest")
        self.assertEqual(bindings["VISION_OPENAI_API_KEY"], "pw-vision-openai:latest")
        self.assertEqual(bindings["TAVILY_API_KEY"], "pw-tavily:latest")
        self.assertEqual(bindings["BACKEND_BEARER_TOKEN"], "pw-bearer:latest")
        self.assertEqual(bindings["BACKEND_DATABASE_URL"], "pw-db:latest")

    def test_runtime_env_excludes_provider_secret_and_deprecated_alias_keys(self) -> None:
        env_values = OrderedDict(
            [
                ("REALTIME_PROVIDER", "openai"),
                ("VISION_MEMORY_ENABLED", "true"),
                ("VISION_MEMORY_PROVIDER", "openai"),
                ("REALTIME_TOOLING_ENABLED", "true"),
                ("REALTIME_WEB_SEARCH_PROVIDER", "tavily"),
                ("OPENAI_API_KEY", "openai-key"),
                ("VISION_OPENAI_API_KEY", "vision-key"),
                ("VISION_GEMINI_API_KEY", "unselected-gemini-key"),
                ("TAVILY_API_KEY", "tavily-key"),
                ("VISION_PROVIDER_API_KEY", "deprecated-vision-key"),
                ("MISTRAL_API_KEY", "deprecated-mistral-key"),
                ("BACKEND_BEARER_TOKEN", "bearer"),
                ("BACKEND_DATABASE_URL", "postgres://db"),
            ]
        )

        runtime_env = build_runtime_env_vars(
            env_values=env_values,
            config=self._config(),
            bucket_name="managed-bucket",
        )

        self.assertNotIn("OPENAI_API_KEY", runtime_env)
        self.assertNotIn("VISION_OPENAI_API_KEY", runtime_env)
        self.assertNotIn("VISION_GEMINI_API_KEY", runtime_env)
        self.assertNotIn("TAVILY_API_KEY", runtime_env)
        self.assertNotIn("VISION_PROVIDER_API_KEY", runtime_env)
        self.assertNotIn("MISTRAL_API_KEY", runtime_env)
        self.assertEqual(runtime_env["BACKEND_STORAGE_BACKEND"], "managed")
        self.assertEqual(runtime_env["BACKEND_OBJECT_STORE_NAME"], "managed-bucket")
        self.assertEqual(runtime_env["BACKEND_OBJECT_STORE_BUCKET"], "managed-bucket")


if __name__ == "__main__":
    unittest.main()

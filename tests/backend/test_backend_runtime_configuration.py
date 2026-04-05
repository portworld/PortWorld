from __future__ import annotations

from types import SimpleNamespace
import unittest
import unittest.mock as mock

from backend.bootstrap.runtime import check_runtime_configuration


class _FakeExtensionHealth:
    def __init__(self, *, ok: bool, runtime_prerequisites: object) -> None:
        self.ok = ok
        self.runtime_prerequisites = runtime_prerequisites

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "runtime_prerequisites": {
                "ok": self.runtime_prerequisites.ok,
                "node_launcher_enabled_count": self.runtime_prerequisites.node_launcher_enabled_count,
            },
            "records": [],
        }


class RuntimeConfigurationTests(unittest.TestCase):
    def test_warns_when_tooling_enabled_but_web_search_is_disabled(self) -> None:
        runtime_prerequisites = SimpleNamespace(ok=True, node_launcher_enabled_count=0)
        tooling_runtime = SimpleNamespace(
            web_search_provider="tavily",
            web_search_enabled=False,
            extension_health=_FakeExtensionHealth(ok=True, runtime_prerequisites=runtime_prerequisites),
        )
        storage = mock.Mock()
        storage.is_local_backend = False
        dependencies = SimpleNamespace(
            storage_info=SimpleNamespace(backend="managed", details={"backend": "managed"}),
            storage=storage,
            realtime_provider_factory=mock.Mock(provider_name="openai"),
            realtime_tooling_runtime=tooling_runtime,
        )
        dependencies.realtime_provider_factory.validate_configuration.return_value = None
        settings = SimpleNamespace(
            vision_memory_enabled=False,
            memory_consolidation_enabled=False,
            realtime_tooling_enabled=True,
        )

        with mock.patch("backend.bootstrap.runtime.build_runtime_dependencies", return_value=dependencies):
            result = check_runtime_configuration(settings)

        self.assertTrue(result.ok)
        self.assertEqual(result.web_search_provider, "tavily")
        self.assertIn(
            "REALTIME_TOOLING_ENABLED is true but web_search is disabled",
            result.warnings[0],
        )

    def test_full_readiness_reports_local_storage_paths_and_extension_warnings(self) -> None:
        runtime_prerequisites = SimpleNamespace(ok=False, node_launcher_enabled_count=1)
        tooling_runtime = SimpleNamespace(
            web_search_provider="tavily",
            web_search_enabled=True,
            extension_health=_FakeExtensionHealth(ok=False, runtime_prerequisites=runtime_prerequisites),
        )
        storage_paths = mock.Mock()
        storage_paths.to_dict.return_value = {"data_root": "/tmp/backend-data"}
        storage = mock.Mock()
        storage.is_local_backend = True
        storage.local_storage_paths.return_value = storage_paths
        dependencies = SimpleNamespace(
            storage_info=SimpleNamespace(backend="local", details={"backend": "local"}),
            storage=storage,
            realtime_provider_factory=mock.Mock(provider_name="openai"),
            realtime_tooling_runtime=tooling_runtime,
        )
        dependencies.realtime_provider_factory.validate_configuration.return_value = None
        settings = SimpleNamespace(
            vision_memory_enabled=False,
            memory_consolidation_enabled=False,
            realtime_tooling_enabled=True,
        )

        with (
            mock.patch("backend.bootstrap.runtime.build_runtime_dependencies", return_value=dependencies),
            mock.patch("backend.bootstrap.runtime._probe_tooling_runtime_extensions") as probe_extensions,
        ):
            result = check_runtime_configuration(settings, full_readiness=True)

        storage.bootstrap.assert_called_once()
        probe_extensions.assert_called_once_with(tooling_runtime)
        self.assertFalse(result.ok)
        self.assertEqual(result.check_mode, "full_readiness")
        self.assertTrue(result.storage_bootstrap_probe)
        self.assertEqual(result.storage_paths, {"data_root": "/tmp/backend-data"})
        self.assertEqual(result.extension_health["ok"], False)
        self.assertTrue(
            any("node/npm/npx" in warning for warning in result.warnings),
        )
        self.assertTrue(
            any("enabled extensions failed to load" in warning for warning in result.warnings),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest import TestCase, mock, skipUnless

from backend.bootstrap.runtime import check_runtime_configuration
from backend.core.settings import Settings
from backend.extensions.manifest import ExtensionManifestError, load_extension_manifest
from backend.extensions.models import MCPServerSpec
from backend.extensions.runtime import collect_runtime_prerequisites, open_mcp_handle


class BackendExtensionManifestNodeLauncherTests(TestCase):
    def _write_manifest(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "extensions.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_parser_accepts_stdio_npx_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = self._write_manifest(
                Path(temp_dir),
                {
                    "schema_version": 1,
                    "installed": [
                        {
                            "id": "node-fetch",
                            "kind": "mcp_server",
                            "source": "catalog",
                            "enabled": True,
                            "mcp_server": {
                                "transport": "stdio",
                                "launcher": "npx",
                                "package": "@modelcontextprotocol/server-fetch",
                                "package_version": "0.4.0",
                                "args": ["--help"],
                            },
                        }
                    ],
                },
            )

            manifest = load_extension_manifest(manifest_path)
            mcp = manifest.extensions[0].mcp_server
            assert mcp is not None
            self.assertEqual(mcp.transport, "stdio")
            self.assertEqual(mcp.launcher, "npx")
            self.assertEqual(mcp.package, "@modelcontextprotocol/server-fetch")
            self.assertEqual(mcp.package_version, "0.4.0")
            self.assertEqual(mcp.args, ("--help",))

    def test_parser_rejects_npx_launcher_without_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = self._write_manifest(
                Path(temp_dir),
                {
                    "schema_version": 1,
                    "installed": [
                        {
                            "id": "bad-node-extension",
                            "kind": "mcp_server",
                            "source": "catalog",
                            "enabled": True,
                            "mcp_server": {
                                "transport": "stdio",
                                "launcher": "npx",
                            },
                        }
                    ],
                },
            )

            with self.assertRaises(ExtensionManifestError):
                load_extension_manifest(manifest_path)

    @mock.patch("backend.extensions.runtime.shutil.which")
    def test_runtime_prerequisites_report_missing_node_binaries(self, which_mock: mock.Mock) -> None:
        which_mock.return_value = None

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = self._write_manifest(
                Path(temp_dir),
                {
                    "schema_version": 1,
                    "installed": [
                        {
                            "id": "node-filesystem",
                            "kind": "mcp_server",
                            "source": "catalog",
                            "enabled": True,
                            "mcp_server": {
                                "transport": "stdio",
                                "launcher": "npx",
                                "package": "@modelcontextprotocol/server-filesystem",
                                "package_version": "2026.1.14",
                                "args": ["."],
                            },
                        }
                    ],
                },
            )

            manifest = load_extension_manifest(manifest_path)
            prerequisites = collect_runtime_prerequisites(manifest)

        self.assertEqual(prerequisites.node_launcher_enabled_count, 1)
        self.assertEqual(prerequisites.required_binaries, ("node", "npm", "npx"))
        self.assertEqual(prerequisites.missing_binaries, ("node", "npm", "npx"))
        self.assertFalse(prerequisites.ok)

    @mock.patch("backend.extensions.runtime.shutil.which")
    def test_check_config_reports_backend_runtime_node_prerequisites(self, which_mock: mock.Mock) -> None:
        def which_side_effect(name: str) -> str | None:
            if name in {"node", "npm"}:
                return f"/usr/bin/{name}"
            if name == "npx":
                return None
            return None

        which_mock.side_effect = which_side_effect

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(
                root,
                {
                    "schema_version": 1,
                    "installed": [
                        {
                            "id": "node-filesystem",
                            "kind": "mcp_server",
                            "source": "catalog",
                            "enabled": True,
                            "mcp_server": {
                                "transport": "stdio",
                                "launcher": "npx",
                                "package": "@modelcontextprotocol/server-filesystem",
                                "package_version": "2026.1.14",
                                "args": ["."],
                            },
                        }
                    ],
                },
            )

            env = {
                "OPENAI_API_KEY": "test-key",
                "REALTIME_TOOLING_ENABLED": "true",
                "PORTWORLD_EXTENSIONS_MANIFEST": str(manifest_path),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                settings = Settings.from_env()
                result = check_runtime_configuration(settings)

        self.assertFalse(result.ok)
        extension_health = result.extension_health
        assert extension_health is not None
        runtime_prerequisites = extension_health["runtime_prerequisites"]
        self.assertEqual(runtime_prerequisites["node_launcher_enabled_count"], 1)
        self.assertEqual(runtime_prerequisites["missing_binaries"], ["npx"])
        self.assertIn(
            "Enabled Node MCP extensions require node/npm/npx in the backend runtime environment.",
            result.warnings,
        )

    @skipUnless(
        shutil.which("node") and shutil.which("npm") and shutil.which("npx"),
        "Node MCP prerequisites are not available on PATH.",
    )
    def test_live_filesystem_server_connects_and_lists_temp_directory(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                (root / "hello.txt").write_text("hello", encoding="utf-8")
                spec = MCPServerSpec(
                    transport="stdio",
                    launcher="npx",
                    package="@modelcontextprotocol/server-filesystem",
                    package_version="2026.1.14",
                    args=(str(root),),
                    startup_timeout_seconds=20.0,
                )
                handle = await open_mcp_handle(extension_id="node-filesystem", spec=spec)
                try:
                    tools = await handle.session.list_tools()
                    tool_names = {str(tool.name) for tool in tools.tools}
                    self.assertIn("list_directory", tool_names)
                    allowed = await handle.call_tool(
                        name="list_allowed_directories",
                        arguments={},
                    )
                    allowed_payload = allowed.model_dump(mode="json")
                    self.assertFalse(allowed_payload["isError"])
                    allowed_text = "\n".join(
                        item["text"]
                        for item in allowed_payload["content"]
                        if item.get("type") == "text"
                    )
                    allowed_dir = allowed_text.splitlines()[-1].strip()

                    listed = await handle.call_tool(
                        name="list_directory",
                        arguments={"path": allowed_dir},
                    )
                    listed_payload = listed.model_dump(mode="json")
                    self.assertFalse(listed_payload["isError"])
                    listed_text = "\n".join(
                        item["text"]
                        for item in listed_payload["content"]
                        if item.get("type") == "text"
                    )
                    self.assertIn("hello.txt", listed_text)
                finally:
                    await handle.close()

        asyncio.run(_run())

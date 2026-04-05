from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from portworld_cli.extensions.manifest import ExtensionManifestError, load_manifest


class ExtensionManifestNodeLauncherTests(unittest.TestCase):
    def _write_manifest(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / ".portworld" / "extensions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_parse_stdio_npx_launcher_entry(self) -> None:
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
                                "package_version": "1.2.3",
                                "args": ["--port", "0"],
                            },
                        }
                    ],
                    "local_definitions": [],
                },
            )

            manifest = load_manifest(manifest_path)
            entry = manifest.installed[0]
            assert entry.mcp_server is not None
            self.assertEqual(entry.mcp_server.transport, "stdio")
            self.assertEqual(entry.mcp_server.launcher, "npx")
            self.assertEqual(
                entry.mcp_server.package,
                "@modelcontextprotocol/server-fetch",
            )
            self.assertEqual(entry.mcp_server.package_version, "1.2.3")
            self.assertEqual(entry.mcp_server.args, ("--port", "0"))

    def test_parse_stdio_npm_exec_launcher_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = self._write_manifest(
                Path(temp_dir),
                {
                    "schema_version": 1,
                    "installed": [
                        {
                            "id": "node-filesystem",
                            "kind": "mcp_server",
                            "source": "local",
                            "enabled": True,
                            "mcp_server": {
                                "transport": "stdio",
                                "launcher": "npm_exec",
                                "package": "@modelcontextprotocol/server-filesystem",
                                "args": ["--root", "/tmp"],
                            },
                        }
                    ],
                    "local_definitions": [],
                },
            )

            manifest = load_manifest(manifest_path)
            entry = manifest.installed[0]
            assert entry.mcp_server is not None
            self.assertEqual(entry.mcp_server.launcher, "npm_exec")
            self.assertEqual(
                entry.mcp_server.package,
                "@modelcontextprotocol/server-filesystem",
            )
            self.assertEqual(entry.mcp_server.args, ("--root", "/tmp"))

    def test_stdio_node_launcher_requires_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = self._write_manifest(
                Path(temp_dir),
                {
                    "schema_version": 1,
                    "installed": [
                        {
                            "id": "broken-node-launcher",
                            "kind": "mcp_server",
                            "source": "catalog",
                            "enabled": True,
                            "mcp_server": {
                                "transport": "stdio",
                                "launcher": "npx",
                                "args": ["--help"],
                            },
                        }
                    ],
                    "local_definitions": [],
                },
            )

            with self.assertRaises(ExtensionManifestError):
                load_manifest(manifest_path)


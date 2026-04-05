from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock as mock

from portworld_cli.extensions.doctor import run_extension_doctor


class ExtensionDoctorNodeLauncherTests(unittest.TestCase):
    def _write_manifest(self, root: Path, *, launcher: str = "npx") -> Path:
        path = root / ".portworld" / "extensions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
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
                                "launcher": launcher,
                                "package": "@modelcontextprotocol/server-fetch",
                            },
                        }
                    ],
                    "local_definitions": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    @mock.patch("portworld_cli.extensions.doctor.shutil.which")
    def test_doctor_warns_when_npx_not_available(
        self,
        which_mock: mock.Mock,
    ) -> None:
        def which_side_effect(name: str) -> str | None:
            if name == "node":
                return "/usr/bin/node"
            if name == "npm":
                return "/usr/bin/npm"
            if name == "npx":
                return None
            return None

        which_mock.side_effect = which_side_effect

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            result = run_extension_doctor(
                manifest_path=manifest_path,
                python_install_dir=root / ".portworld" / "extensions" / "python",
            )

        npx_related = [
            check
            for check in result.checks
            if "npx" in check.id.lower() or "npx" in check.message.lower()
        ]
        self.assertTrue(npx_related, "Expected npx-related checks to be emitted.")
        self.assertTrue(
            any(check.status in {"warn", "fail"} for check in npx_related),
            "Expected missing npx to degrade doctor status.",
        )

    @mock.patch("portworld_cli.extensions.doctor.shutil.which")
    def test_doctor_emits_actionable_install_guidance_when_node_tooling_is_missing(
        self,
        which_mock: mock.Mock,
    ) -> None:
        which_mock.return_value = None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            result = run_extension_doctor(
                manifest_path=manifest_path,
                python_install_dir=root / ".portworld" / "extensions" / "python",
            )

        missing_tool_checks = [
            check
            for check in result.checks
            if check.id.endswith("_mcp_node_binary")
            or check.id.endswith("_mcp_npm_binary")
            or check.id.endswith("_mcp_npx_binary")
        ]
        self.assertEqual(
            len(missing_tool_checks),
            3,
            "Expected node, npm, and npx prerequisite checks.",
        )
        self.assertTrue(
            all(check.status in {"warn", "fail"} for check in missing_tool_checks),
            "Expected missing Node tooling to produce warn/fail checks.",
        )
        self.assertTrue(
            all((check.action or "").strip() for check in missing_tool_checks),
            "Expected missing Node tooling checks to include actionable guidance.",
        )
        self.assertTrue(
            all("install.sh" in (check.action or "") for check in missing_tool_checks),
            "Expected guidance to point users to install.sh bootstrap flow.",
        )

    @mock.patch("portworld_cli.extensions.doctor.shutil.which")
    def test_doctor_passes_npx_prereqs_when_available(
        self,
        which_mock: mock.Mock,
    ) -> None:
        def which_side_effect(name: str) -> str | None:
            if name in {"node", "npm", "npx"}:
                return f"/usr/bin/{name}"
            return None

        which_mock.side_effect = which_side_effect

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root)
            result = run_extension_doctor(
                manifest_path=manifest_path,
                python_install_dir=root / ".portworld" / "extensions" / "python",
            )

        npx_related = [
            check
            for check in result.checks
            if "npx" in check.id.lower() or "npx" in check.message.lower()
        ]
        self.assertTrue(npx_related, "Expected npx-related checks to be emitted.")
        self.assertTrue(
            any(check.status == "pass" for check in npx_related),
            "Expected npx checks to pass when tooling is available.",
        )

    @mock.patch("portworld_cli.extensions.doctor.shutil.which")
    def test_doctor_checks_node_and_npm_for_npm_exec_launcher_without_npx_requirement(
        self,
        which_mock: mock.Mock,
    ) -> None:
        def which_side_effect(name: str) -> str | None:
            if name in {"node", "npm"}:
                return f"/usr/bin/{name}"
            if name == "npx":
                return None
            return None

        which_mock.side_effect = which_side_effect

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._write_manifest(root, launcher="npm_exec")
            result = run_extension_doctor(
                manifest_path=manifest_path,
                python_install_dir=root / ".portworld" / "extensions" / "python",
            )

        npx_checks = [check for check in result.checks if check.id.endswith("_mcp_npx_binary")]
        self.assertFalse(
            npx_checks,
            "Expected launcher=npm_exec to skip npx binary checks.",
        )
        npm_check = next(
            (check for check in result.checks if check.id.endswith("_mcp_npm_binary")),
            None,
        )
        self.assertIsNotNone(npm_check, "Expected npm binary check for launcher=npm_exec.")
        self.assertEqual(npm_check.status, "pass")

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from portworld_cli.extensions.types import EXTENSION_KIND_TOOL_PACKAGE, ExtensionManifest


class ExtensionInstallError(RuntimeError):
    """Raised when extension package installation fails."""


@dataclass(frozen=True, slots=True)
class InstallSummary:
    package_count: int
    install_dir: Path

    def to_payload(self) -> dict[str, object]:
        return {
            "package_count": self.package_count,
            "install_dir": str(self.install_dir),
        }


def reconcile_python_extension_install_dir(
    manifest: ExtensionManifest,
    *,
    install_dir: Path,
) -> InstallSummary:
    install_dir.mkdir(parents=True, exist_ok=True)
    _reset_dir_contents(install_dir)

    package_refs: list[str] = []
    for entry in manifest.installed:
        if entry.kind != EXTENSION_KIND_TOOL_PACKAGE:
            continue
        if entry.tool_package is None:
            continue
        package_ref = entry.tool_package.package_ref.strip()
        if not package_ref:
            continue
        package_refs.append(package_ref)

    for package_ref in package_refs:
        _install_with_uv(package_ref=package_ref, install_dir=install_dir)

    return InstallSummary(
        package_count=len(package_refs),
        install_dir=install_dir,
    )


def _reset_dir_contents(path: Path) -> None:
    for child in path.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _install_with_uv(*, package_ref: str, install_dir: Path) -> None:
    completed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(install_dir),
            package_ref,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return
    message = (completed.stderr or completed.stdout).strip()
    if not message:
        message = f"uv pip install failed for {package_ref!r}"
    raise ExtensionInstallError(message)


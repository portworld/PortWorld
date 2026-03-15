from __future__ import annotations

import os

REPO_OWNER = "armapidus"
REPO_NAME = "PortWorld"
GITHUB_REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
INSTALLER_SCRIPT_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/install.sh"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
DEFAULT_PYPI_PACKAGE_NAME = "portworld"
FALLBACK_PYPI_PACKAGE_NAME = "portworld-cli"


def active_pypi_package_name() -> str:
    value = os.environ.get("PORTWORLD_PYPI_PACKAGE", "").strip()
    if value:
        return value
    return DEFAULT_PYPI_PACKAGE_NAME


def package_name_candidates() -> tuple[str, ...]:
    candidates = [active_pypi_package_name(), DEFAULT_PYPI_PACKAGE_NAME, FALLBACK_PYPI_PACKAGE_NAME]
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return tuple(deduped)


def tagged_archive_url(tag: str) -> str:
    return f"{GITHUB_REPO_URL}/archive/refs/tags/{tag}.zip"

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.request import Request, urlopen
import json

from portworld_cli.release.identity import LATEST_RELEASE_API_URL


@dataclass(frozen=True, slots=True)
class ReleaseLookupResult:
    status: str
    target_version: str | None
    update_available: bool | None


def fetch_latest_release_payload(*, api_url: str = LATEST_RELEASE_API_URL) -> object:
    request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "portworld-cli",
        },
    )
    with urlopen(request, timeout=10.0) as response:
        return json.load(response)


def extract_latest_release_tag(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str):
        return None
    normalized = tag_name.strip()
    return normalized or None


def compare_numeric_versions(current_version: str, target_version: str) -> bool | None:
    current_release = _parse_release_version(current_version)
    target_release = _parse_release_version(target_version)
    if current_release is None or target_release is None:
        return None
    if target_release.is_prerelease:
        return None
    if target_release.parts != current_release.parts:
        return target_release.parts > current_release.parts
    return current_release.is_prerelease


def normalize_numeric_package_version(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)*", normalized):
        return None
    return normalized


def _parse_numeric_version_parts(value: str) -> tuple[int, ...] | None:
    normalized = normalize_numeric_package_version(value)
    if normalized is None:
        return None
    return tuple(int(part) for part in normalized.split("."))


@dataclass(frozen=True, slots=True)
class _ParsedReleaseVersion:
    parts: tuple[int, ...]
    is_prerelease: bool


def _parse_release_version(value: str) -> _ParsedReleaseVersion | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    match = re.fullmatch(
        r"v?(?P<release>\d+(?:\.\d+)*)(?P<suffix>[-_.]?[A-Za-z][A-Za-z0-9.\-]*)?",
        normalized,
    )
    if match is None:
        return None
    release = match.group("release")
    suffix = match.group("suffix") or ""
    return _ParsedReleaseVersion(
        parts=tuple(int(part) for part in release.split(".")),
        is_prerelease=bool(suffix),
    )

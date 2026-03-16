from __future__ import annotations

from dataclasses import dataclass
import json
from importlib import resources
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend import __version__
from portworld_cli.envfile import (
    EnvTemplate,
    EnvWriteResult,
    load_env_template_text,
    parse_env_file,
    write_canonical_env,
)
from portworld_cli.paths import ProjectPaths, WorkspacePaths
from portworld_cli.project_config import ProjectConfig, write_project_config
from portworld_cli.release_identity import LATEST_RELEASE_API_URL, REPO_OWNER


DEFAULT_STACK_NAME = "default"
DEFAULT_STACKS_DIR = Path.home() / ".portworld" / "stacks"
PUBLISHED_ENV_FILENAME = ".env"
PUBLISHED_COMPOSE_FILENAME = "docker-compose.yml"
DEFAULT_PUBLISHED_HOST_PORT = 8080
BACKEND_IMAGE_REPOSITORY = f"ghcr.io/{REPO_OWNER}/portworld-backend"
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[A-Za-z0-9.\-]+)?$")


class PublishedWorkspaceError(RuntimeError):
    """Raised when published workspace bootstrap or runtime actions fail."""


@dataclass(frozen=True, slots=True)
class PublishedReleaseRef:
    release_tag: str
    image_ref: str


@dataclass(frozen=True, slots=True)
class PublishedWorkspaceTarget:
    workspace_root: Path
    stack_name: str


@dataclass(frozen=True, slots=True)
class PublishedComposeStatus:
    available: bool
    running: bool | None
    service_name: str | None
    container_name: str | None
    state: str | None
    health: str | None
    exit_code: int | None
    warning: str | None = None

    def to_payload(self) -> dict[str, object | None]:
        return {
            "available": self.available,
            "running": self.running,
            "service_name": self.service_name,
            "container_name": self.container_name,
            "state": self.state,
            "health": self.health,
            "exit_code": self.exit_code,
            "warning": self.warning,
        }


def load_published_env_template() -> EnvTemplate:
    template_path = resources.files("portworld_cli.templates").joinpath("published.env.template")
    return load_env_template_text(
        Path("portworld_cli/templates/published.env.template"),
        template_path.read_text(encoding="utf-8"),
    )


def render_published_compose(*, image_ref: str, host_port: int) -> str:
    template_path = resources.files("portworld_cli.templates").joinpath(
        "published.docker-compose.yml.template"
    )
    template_text = template_path.read_text(encoding="utf-8")
    return template_text.format(image_ref=image_ref, host_port=host_port)


def resolve_published_workspace_target(
    *,
    explicit_root: Path | None,
    stack_name: str | None,
) -> PublishedWorkspaceTarget:
    if explicit_root is not None:
        root = explicit_root.expanduser().resolve()
        return PublishedWorkspaceTarget(
            workspace_root=root,
            stack_name=root.name,
        )

    normalized_stack = (stack_name or DEFAULT_STACK_NAME).strip()
    if not normalized_stack:
        normalized_stack = DEFAULT_STACK_NAME
    root = (DEFAULT_STACKS_DIR / normalized_stack).expanduser().resolve()
    return PublishedWorkspaceTarget(
        workspace_root=root,
        stack_name=normalized_stack,
    )


def resolve_published_release_ref(requested_tag: str | None) -> PublishedReleaseRef:
    if requested_tag is None:
        release_tag = f"v{__version__}"
    else:
        candidate = requested_tag.strip()
        if not candidate:
            raise PublishedWorkspaceError("--release-tag cannot be empty.")
        if candidate == "latest":
            release_tag = _lookup_latest_release_tag()
        else:
            release_tag = candidate

    if not RELEASE_TAG_RE.match(release_tag):
        raise PublishedWorkspaceError(
            "Published workspaces require a concrete release tag like v0.1.0 or --release-tag latest."
        )
    return PublishedReleaseRef(
        release_tag=release_tag,
        image_ref=f"{BACKEND_IMAGE_REPOSITORY}:{release_tag}",
    )


def prepare_published_workspace_root(
    target: PublishedWorkspaceTarget,
    *,
    force: bool,
) -> WorkspacePaths:
    root = target.workspace_root
    candidate_project = ProjectPaths.from_root(root)
    if not candidate_project.missing_required_markers():
        raise PublishedWorkspaceError(
            f"{root} looks like a PortWorld source checkout. "
            "Choose a non-repo workspace path for runtime_source=published."
        )

    workspace_paths = WorkspacePaths.from_root(root)
    if root.exists():
        entries = tuple(root.iterdir())
        if entries and not workspace_paths.has_workspace_config() and not force:
            raise PublishedWorkspaceError(
                f"{root} already exists and is not an initialized PortWorld workspace. "
                "Use --force to overwrite its published-workspace files or choose a different path."
            )
    else:
        root.mkdir(parents=True, exist_ok=True)
    workspace_paths.cli_dir.mkdir(parents=True, exist_ok=True)
    workspace_paths.cli_state_dir.mkdir(parents=True, exist_ok=True)
    return workspace_paths


def write_published_workspace_artifacts(
    *,
    workspace_paths: WorkspacePaths,
    project_config: ProjectConfig,
    env_template: EnvTemplate,
    env_overrides: dict[str, str],
    compose_content: str,
    force: bool,
) -> tuple[EnvWriteResult, Path | None]:
    if workspace_paths.source_project_paths is not None:
        raise PublishedWorkspaceError("Published workspace generation cannot target a source checkout.")

    existing_env = parse_env_file(workspace_paths.workspace_env_file, template=env_template)
    env_write_result = write_canonical_env(
        workspace_paths.workspace_env_file,
        template=env_template,
        existing_env=existing_env,
        overrides=env_overrides,
    )
    write_project_config(workspace_paths.project_config_file, project_config)
    compose_backup_path = _write_text_file(
        workspace_paths.compose_file,
        compose_content,
        force=force,
    )
    return env_write_result, compose_backup_path


# Compatibility wrappers for moved runtime helpers.
def build_compose_command(workspace_root: Path, *args: str) -> list[str]:
    from portworld_cli.runtime.published import build_compose_command as _build_compose_command

    return _build_compose_command(workspace_root, *args)


def inspect_published_compose_status(workspace_root: Path) -> PublishedComposeStatus:
    from portworld_cli.runtime.published import inspect_published_compose_status as _inspect

    status = _inspect(workspace_root)
    return PublishedComposeStatus(
        available=status.available,
        running=status.running,
        service_name=status.service_name,
        container_name=status.container_name,
        state=status.state,
        health=status.health,
        exit_code=status.exit_code,
        warning=status.warning,
    )


def run_backend_compose_cli(
    workspace_root: Path,
    *,
    backend_args: list[str],
    output_mount: tuple[Path, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    from portworld_cli.runtime.published import run_backend_compose_cli as _run_backend_compose_cli

    return _run_backend_compose_cli(
        workspace_root,
        backend_args=backend_args,
        output_mount=output_mount,
    )


def parse_backend_cli_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    from portworld_cli.runtime.published import parse_backend_cli_json as _parse_backend_cli_json

    try:
        return _parse_backend_cli_json(completed)
    except RuntimeError as exc:
        raise PublishedWorkspaceError(str(exc)) from exc


def coerce_backend_cli_payload(
    completed: subprocess.CompletedProcess[str],
    *,
    default_message: str,
) -> dict[str, Any]:
    from portworld_cli.runtime.published import coerce_backend_cli_payload as _coerce_backend_cli_payload

    return _coerce_backend_cli_payload(
        completed,
        default_message=default_message,
    )


def _lookup_latest_release_tag() -> str:
    request = Request(
        LATEST_RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "portworld-cli",
        },
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            payload = json.load(response)
    except (URLError, TimeoutError) as exc:
        raise PublishedWorkspaceError(
            "Failed to resolve the latest PortWorld release tag from GitHub."
        ) from exc

    if not isinstance(payload, dict):
        raise PublishedWorkspaceError("GitHub release lookup returned an unexpected response.")
    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise PublishedWorkspaceError("GitHub release lookup did not include a usable tag_name.")
    return tag_name.strip()


def _write_text_file(path: Path, content: str, *, force: bool) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise PublishedWorkspaceError(f"{path} is a directory; expected a file path.")
    if path.exists() and not force:
        backup_path = path.with_name(f"{path.name}.bak.{_now_ms()}")
    else:
        backup_path = None

    if path.exists():
        if backup_path is not None:
            shutil.copy2(path, backup_path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return backup_path


def _now_ms() -> int:
    return int(time.time() * 1000)

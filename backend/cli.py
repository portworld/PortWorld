from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from backend.api.app import create_app_from_settings
from backend.bootstrap.memory_export import (
    build_local_canonical_memory_artifacts,
    write_memory_export_zip,
)
from backend.bootstrap.runtime import (
    build_backend_storage,
    check_runtime_configuration,
)
from backend.core.settings import Settings, load_environment_files
from backend.core.storage import now_ms
from backend.memory.lifecycle import CROSS_SESSION_MEMORY_TEMPLATE, USER_MEMORY_TEMPLATE


def _json_dump(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = Settings.from_env()
    if args.host is not None or args.port is not None or args.log_level is not None:
        settings = replace(
            settings,
            host=args.host or settings.host,
            port=args.port if args.port is not None else settings.port,
            log_level=args.log_level or settings.log_level,
        )
    app = create_app_from_settings(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips=",".join(settings.backend_forwarded_allow_ips),
    )
    return 0


def _check_config(args: argparse.Namespace) -> int:
    result = check_runtime_configuration(
        Settings.from_env(),
        full_readiness=bool(args.full_readiness),
    )
    _json_dump(result.to_dict())
    return 0


def _bootstrap_storage(_: argparse.Namespace) -> int:
    _, storage = build_backend_storage(Settings.from_env())
    if not storage.is_local_backend:
        raise RuntimeError(
            "bootstrap-storage is only supported when BACKEND_STORAGE_BACKEND=local. "
            "Managed metadata bootstrap runs through check-config --full or normal runtime startup."
        )
    result = storage.bootstrap()
    _json_dump({"status": "ok", **result.to_dict()})
    return 0


def _export_memory(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _, storage = build_backend_storage(settings)
    storage.bootstrap()
    artifacts = storage.list_memory_export_artifacts()
    if storage.is_local_backend:
        canonical_artifacts = build_local_canonical_memory_artifacts(
            data_root=settings.backend_data_dir
        )
        by_path = {artifact.relative_path: artifact for artifact in artifacts}
        for artifact in canonical_artifacts:
            by_path[artifact.relative_path] = artifact
        artifacts = sorted(
            by_path.values(),
            key=lambda artifact: (
                artifact.session_id or "",
                artifact.relative_path,
                artifact.artifact_kind,
            ),
        )
    output_path = (
        Path(args.output)
        if args.output is not None
        else Path.cwd() / f"portworld-memory-export-{now_ms()}.zip"
    )
    export_path = write_memory_export_zip(
        artifacts=artifacts,
        session_retention_days=settings.backend_session_memory_retention_days,
        output_path=output_path,
    )
    _json_dump(
        {
            "status": "ok",
            "artifact_count": len(artifacts),
            "export_path": str(export_path),
        }
    )
    return 0


def _migrate_file_only_memory(_: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _, storage = build_backend_storage(settings)
    if not storage.is_local_backend:
        raise RuntimeError(
            "migrate-file-only-memory is only supported when BACKEND_STORAGE_BACKEND=local."
        )

    data_root = settings.backend_data_dir
    memory_root = data_root / "memory"
    sessions_root = memory_root / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)

    migrated_files: list[str] = []
    created_templates: list[str] = []

    for destination, template in (
        (memory_root / "USER.md", USER_MEMORY_TEMPLATE),
        (memory_root / "CROSS_SESSION.md", CROSS_SESSION_MEMORY_TEMPLATE),
    ):
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(template, encoding="utf-8")
            created_templates.append(str(destination.relative_to(data_root)))

    legacy_user_markdown = data_root / "user" / "user_profile.md"
    user_destination = memory_root / "USER.md"
    if legacy_user_markdown.exists() and legacy_user_markdown.is_file():
        _copy_if_different(src=legacy_user_markdown, dst=user_destination)
        migrated_files.append(str(user_destination.relative_to(data_root)))

    legacy_session_root = data_root / "session"
    if legacy_session_root.exists():
        for legacy_session_dir in sorted(path for path in legacy_session_root.iterdir() if path.is_dir()):
            session_id = legacy_session_dir.name
            canonical_session_dir = sessions_root / session_id
            canonical_session_dir.mkdir(parents=True, exist_ok=True)
            mappings = (
                (legacy_session_dir / "short_term_memory.md", canonical_session_dir / "SHORT_TERM.md"),
                (legacy_session_dir / "session_memory.md", canonical_session_dir / "LONG_TERM.md"),
                (legacy_session_dir / "vision_events.jsonl", canonical_session_dir / "EVENTS.ndjson"),
            )
            for src, dst in mappings:
                if not src.exists() or not src.is_file():
                    continue
                _copy_if_different(src=src, dst=dst)
                migrated_files.append(str(dst.relative_to(data_root)))

    _json_dump(
        {
            "status": "ok",
            "data_root": str(data_root),
            "migrated_files_count": len(migrated_files),
            "migrated_files": migrated_files,
            "created_templates_count": len(created_templates),
            "created_templates": created_templates,
        }
    )
    return 0


def _migrate_storage_layout(_: argparse.Namespace) -> int:
    _, storage = build_backend_storage(Settings.from_env())
    if not storage.is_local_backend:
        raise RuntimeError(
            "migrate-storage-layout is only supported when BACKEND_STORAGE_BACKEND=local."
        )
    storage.bootstrap()
    result = storage.migrate_legacy_storage_layout()
    _json_dump(
        {
            "status": "ok",
            **result,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PortWorld backend operator CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the backend HTTP/WebSocket server.")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--log-level", default=None)
    serve_parser.set_defaults(handler=_serve)

    check_parser = subparsers.add_parser(
        "check-config",
        help="Validate backend configuration and print a reproducible summary.",
    )
    check_parser.add_argument(
        "--full-readiness",
        action="store_true",
        help="Run full readiness checks, including a storage bootstrap probe.",
    )
    check_parser.set_defaults(handler=_check_config)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-storage",
        help="Create storage directories, SQLite schema, and profile scaffold.",
    )
    bootstrap_parser.set_defaults(handler=_bootstrap_storage)

    export_parser = subparsers.add_parser(
        "export-memory",
        help="Write a memory export zip to disk.",
    )
    export_parser.add_argument("--output", default=None)
    export_parser.set_defaults(handler=_export_memory)

    migrate_parser = subparsers.add_parser(
        "migrate-file-only-memory",
        help="Migrate legacy local memory files into canonical memory/ markdown paths.",
    )
    migrate_parser.set_defaults(handler=_migrate_file_only_memory)

    migrate_layout_parser = subparsers.add_parser(
        "migrate-storage-layout",
        help="Migrate legacy session/vision directories to hashed storage paths.",
    )
    migrate_layout_parser.set_defaults(handler=_migrate_storage_layout)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_environment_files()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        _json_dump(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return 1


def _copy_if_different(*, src: Path, dst: Path) -> None:
    if dst.exists() and dst.read_bytes() == src.read_bytes():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


if __name__ == "__main__":
    raise SystemExit(main())

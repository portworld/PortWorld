from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from backend.api.app import create_app_from_settings
from backend.bootstrap.ops import (
    bootstrap_backend_storage,
    check_backend_config,
    export_backend_memory,
    migrate_backend_storage_layout,
)
from backend.bootstrap.runtime import (
    ConfigCheckResult,
)
from backend.core.settings import Settings, load_environment_files


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
    result: ConfigCheckResult = check_backend_config(
        Settings.from_env(),
        full_readiness=bool(args.full_readiness),
    )
    _json_dump(result.to_dict())
    return 0


def _bootstrap_storage(_: argparse.Namespace) -> int:
    result = bootstrap_backend_storage(Settings.from_env())
    _json_dump({"status": "ok", **result.to_dict()})
    return 0


def _export_memory(args: argparse.Namespace) -> int:
    payload = export_backend_memory(
        Settings.from_env(),
        output_path=(
        Path(args.output)
        if args.output is not None
        else None
        ),
    )
    _json_dump(payload)
    return 0


def _migrate_storage_layout(_: argparse.Namespace) -> int:
    _json_dump(migrate_backend_storage_layout(Settings.from_env()))
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
        "migrate-storage-layout",
        help="Migrate legacy session/vision directories to hashed storage paths.",
    )
    migrate_parser.set_defaults(handler=_migrate_storage_layout)

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


if __name__ == "__main__":
    raise SystemExit(main())

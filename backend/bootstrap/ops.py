from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.bootstrap.memory_export import write_memory_export_zip
from backend.bootstrap.runtime import build_backend_storage, check_runtime_configuration
from backend.core.settings import Settings
from backend.core.storage import now_ms
from backend.infrastructure.storage import StorageBootstrapResult


def check_backend_config(
    settings: Settings,
    *,
    full_readiness: bool = False,
):
    return check_runtime_configuration(
        settings,
        full_readiness=full_readiness,
    )


def bootstrap_backend_storage(settings: Settings) -> StorageBootstrapResult:
    _, storage = build_backend_storage(settings)
    if not storage.is_local_backend:
        raise RuntimeError(
            "bootstrap-storage is only supported when BACKEND_STORAGE_BACKEND=local. "
            "Managed metadata bootstrap runs through check-config --full or normal runtime startup."
        )
    return storage.bootstrap()


def export_backend_memory(
    settings: Settings,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    _, storage = build_backend_storage(settings)
    storage.bootstrap()
    artifacts = storage.list_memory_export_artifacts()
    final_output_path = output_path or (Path.cwd() / f"portworld-memory-export-{now_ms()}.zip")
    export_path = write_memory_export_zip(
        artifacts=artifacts,
        session_retention_days=settings.backend_session_memory_retention_days,
        output_path=final_output_path,
    )
    return {
        "status": "ok",
        "artifact_count": len(artifacts),
        "export_path": str(export_path),
    }


def migrate_backend_storage_layout(settings: Settings) -> dict[str, Any]:
    _, storage = build_backend_storage(settings)
    if not storage.is_local_backend:
        raise RuntimeError(
            "migrate-storage-layout is only supported when BACKEND_STORAGE_BACKEND=local."
        )
    storage.bootstrap()
    return {
        "status": "ok",
        **storage.migrate_legacy_storage_layout(),
    }

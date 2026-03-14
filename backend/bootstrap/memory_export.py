from __future__ import annotations

import json
import os
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from backend.core.storage import MemoryExportArtifact, now_ms
from backend.memory.lifecycle import MemoryExportManifest


def _validated_arcname(*, artifact: MemoryExportArtifact) -> str:
    raw_path = artifact.relative_path
    if "\x00" in raw_path:
        raise ValueError(
            "Invalid memory export artifact path: null byte is not allowed "
            f"artifact_kind={artifact.artifact_kind!r} session_id={artifact.session_id!r} "
            f"relative_path={raw_path!r}"
        )

    candidate = raw_path.strip()
    if not candidate:
        raise ValueError(
            "Invalid memory export artifact path: empty path is not allowed "
            f"artifact_kind={artifact.artifact_kind!r} session_id={artifact.session_id!r} "
            f"relative_path={raw_path!r}"
        )
    if "\\" in candidate:
        raise ValueError(
            "Invalid memory export artifact path: backslashes are not allowed "
            f"artifact_kind={artifact.artifact_kind!r} session_id={artifact.session_id!r} "
            f"relative_path={raw_path!r}"
        )
    if candidate.startswith("/"):
        raise ValueError(
            "Invalid memory export artifact path: absolute paths are not allowed "
            f"artifact_kind={artifact.artifact_kind!r} session_id={artifact.session_id!r} "
            f"relative_path={raw_path!r}"
        )
    if len(candidate) >= 2 and candidate[1] == ":" and candidate[0].isalpha():
        raise ValueError(
            "Invalid memory export artifact path: drive-prefixed paths are not allowed "
            f"artifact_kind={artifact.artifact_kind!r} session_id={artifact.session_id!r} "
            f"relative_path={raw_path!r}"
        )

    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            "Invalid memory export artifact path: traversal or empty path segments are not allowed "
            f"artifact_kind={artifact.artifact_kind!r} session_id={artifact.session_id!r} "
            f"relative_path={raw_path!r}"
        )
    return "/".join(parts)


def _build_export_manifest(
    *,
    artifacts: list[MemoryExportArtifact],
    session_retention_days: int,
) -> MemoryExportManifest:
    session_ids = tuple(
        sorted(
            {
                artifact.session_id
                for artifact in artifacts
                if artifact.session_id is not None
            }
        )
    )
    included_artifact_kinds = tuple(
        sorted({artifact.artifact_kind for artifact in artifacts})
    )
    return MemoryExportManifest(
        exported_at_ms=now_ms(),
        session_retention_days=session_retention_days,
        session_ids=session_ids,
        included_artifact_kinds=included_artifact_kinds,
    )


def write_memory_export_zip(
    *,
    artifacts: list[MemoryExportArtifact],
    session_retention_days: int,
    output_path: str | Path | None = None,
) -> Path:
    manifest = _build_export_manifest(
        artifacts=artifacts,
        session_retention_days=session_retention_days,
    )
    final_path: Path | None = None
    if output_path is None:
        with tempfile.NamedTemporaryFile(
            prefix="portworld-memory-export-",
            suffix=".zip",
            delete=False,
        ) as handle:
            export_path = Path(handle.name)
    else:
        final_path = Path(output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=final_path.parent,
            delete=False,
        ) as handle:
            export_path = Path(handle.name)

    try:
        with zipfile.ZipFile(export_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for artifact in artifacts:
                archive.writestr(
                    _validated_arcname(artifact=artifact),
                    artifact.read_bytes(),
                )
            archive.writestr(
                "manifest.json",
                json.dumps(asdict(manifest), ensure_ascii=True, indent=2) + "\n",
            )
        if final_path is not None:
            export_path.replace(final_path)
            return final_path
    except Exception:
        cleanup_export_file(export_path)
        raise
    return export_path


def cleanup_export_file(path: str | Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return

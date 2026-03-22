from __future__ import annotations

import json
import os
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

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


def build_local_canonical_memory_artifacts(*, data_root: Path) -> list[MemoryExportArtifact]:
    memory_root = data_root / "memory"
    if not memory_root.exists():
        return []

    artifacts: list[MemoryExportArtifact] = []
    artifacts.extend(
        _canonical_memory_artifact_records(
            data_root=data_root,
            artifact_kind="user_memory_markdown",
            session_id=None,
            paths=(memory_root / "USER.md",),
        )
    )
    artifacts.extend(
        _canonical_memory_artifact_records(
            data_root=data_root,
            artifact_kind="cross_session_memory_markdown",
            session_id=None,
            paths=(memory_root / "CROSS_SESSION.md",),
        )
    )

    sessions_root = memory_root / "sessions"
    if sessions_root.exists():
        for session_dir in sorted(path for path in sessions_root.iterdir() if path.is_dir()):
            session_id = session_dir.name
            artifacts.extend(
                _canonical_memory_artifact_records(
                    data_root=data_root,
                    artifact_kind="short_term_memory_markdown",
                    session_id=session_id,
                    paths=(session_dir / "SHORT_TERM.md",),
                )
            )
            artifacts.extend(
                _canonical_memory_artifact_records(
                    data_root=data_root,
                    artifact_kind="session_memory_markdown",
                    session_id=session_id,
                    paths=(session_dir / "LONG_TERM.md",),
                )
            )
            artifacts.extend(
                _canonical_memory_artifact_records(
                    data_root=data_root,
                    artifact_kind="vision_event_log",
                    session_id=session_id,
                    paths=(session_dir / "EVENTS.ndjson",),
                )
            )
    return artifacts


def _canonical_memory_artifact_records(
    *,
    data_root: Path,
    artifact_kind: str,
    session_id: str | None,
    paths: Iterable[Path],
) -> list[MemoryExportArtifact]:
    records: list[MemoryExportArtifact] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() == ".md":
            content_type = "text/markdown"
        elif path.suffix.lower() == ".ndjson":
            content_type = "application/x-ndjson"
        else:
            content_type = "text/plain"
        records.append(
            MemoryExportArtifact(
                artifact_id=None,
                session_id=session_id,
                artifact_kind=artifact_kind,
                relative_path=str(path.relative_to(data_root)),
                content_type=content_type,
                created_at_ms=None,
                read_bytes=lambda path=path: path.read_bytes(),
            )
        )
    return records

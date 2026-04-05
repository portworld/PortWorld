from __future__ import annotations

from dataclasses import dataclass

from portworld_cli.deploy_artifacts import (
    IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
    derive_published_artifact_repository,
)


@dataclass(frozen=True, slots=True)
class PublishedImageSelection:
    image_source_mode: str
    artifact_repository: str
    image_tag: str
    release_tag: str
    image_ref: str


def resolve_published_image_selection(
    *,
    explicit_tag: str | None,
    artifact_repository: str,
    release_tag: str | None,
    image_ref: str | None,
) -> PublishedImageSelection:
    if explicit_tag is not None:
        raise ValueError(
            "--tag is only supported in runtime_source=source. "
            "Published-mode deploys always use the workspace's pinned release tag."
        )
    normalized_release_tag = _normalize_text(release_tag)
    normalized_image_ref = _normalize_text(image_ref)
    if not normalized_release_tag or not normalized_image_ref:
        raise ValueError(
            "Published-mode deploy requires a pinned release tag and image ref. "
            "Run `portworld init --runtime-source published --release-tag <latest|vX.Y.Z>` first."
        )
    return PublishedImageSelection(
        image_source_mode=IMAGE_SOURCE_MODE_PUBLISHED_RELEASE,
        artifact_repository=derive_published_artifact_repository(artifact_repository),
        image_tag=normalized_release_tag,
        release_tag=normalized_release_tag,
        image_ref=normalized_image_ref,
    )


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


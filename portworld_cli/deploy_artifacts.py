from __future__ import annotations


IMAGE_NAME = "portworld-backend"
IMAGE_SOURCE_MODE_SOURCE_BUILD = "source_build"
IMAGE_SOURCE_MODE_PUBLISHED_RELEASE = "published_release"
PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX = "-ghcr"
GHCR_REMOTE_DOCKER_REPO = "https://ghcr.io"


def derive_published_artifact_repository(base_repository: str) -> str:
    normalized = base_repository.strip()
    if not normalized:
        normalized = "portworld"
    return f"{normalized}{PUBLISHED_ARTIFACT_REPOSITORY_SUFFIX}"


def derive_remote_image_name(image_ref: str, *, fallback_image_name: str) -> str:
    normalized = image_ref.strip()
    if not normalized:
        return fallback_image_name

    without_digest = normalized.split("@", 1)[0]
    if "/" not in without_digest:
        return fallback_image_name

    _, path_with_tag = without_digest.split("/", 1)
    last_segment = path_with_tag.rsplit("/", 1)[-1]
    if ":" in last_segment:
        image_path, _ = path_with_tag.rsplit(":", 1)
        image_path = image_path.strip()
        return image_path or fallback_image_name

    path_with_tag = path_with_tag.strip()
    return path_with_tag or fallback_image_name

from __future__ import annotations


REQUIRED_GCP_SERVICES: tuple[str, ...] = (
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
)

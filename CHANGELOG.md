# Changelog

All notable user-visible changes in this repository are documented in this file.

This project follows a Keep a Changelog style format and uses version tags like
`vX.Y.Z` (including prerelease tags such as `v0.2.0b3`).

GitHub Releases are still published for each tag. The release notes for each tag
must mirror the corresponding section in this changelog.

## [Unreleased]

### Added

- No user-visible additions recorded yet.

### Changed

- No user-visible behavior changes recorded yet.

### Fixed

- No user-visible fixes recorded yet.

### Security

- No user-visible security changes recorded yet.

## [v0.2.2] - 2026-04-05

### Added

- No user-visible additions recorded yet.

### Changed

- No user-visible behavior changes recorded yet.

### Fixed

- TestPyPI smoke installs now let `uv` resolve the requested release version
  across both TestPyPI and PyPI, avoiding false failures when the package name
  already exists on PyPI at an older version.

### Security

- No user-visible security changes recorded yet.

## [v0.2.1] - 2026-04-05

### Added

- No user-visible additions recorded yet.

### Changed

- Backend container builds now use a dedicated backend package manifest and a
  backend-only wheel install path, so the published GHCR image no longer ships
  CLI source or build leftovers.

### Fixed

- The tagged release workflow now checks out the repository before creating the
  GitHub Release, so annotated tag notes can be published correctly.
- Backend image smoke checks now assert that runtime images do not contain
  `/app/portworld_cli`, `/app/build`, or top-level `*.egg-info` leftovers.

### Security

- No user-visible security changes recorded yet.

## [v0.2.0] - 2026-04-05

### Added

- No user-visible additions recorded yet.

### Changed

- Published operator workflows now use the canonical released backend image
  path on both GCP Cloud Run and AWS ECS/Fargate.
- Published workspace runtime defaults now match the documented backend
  defaults.
- Removed pre-v1 compatibility aliases and stale legacy surfaces from the
  supported backend and CLI operator flows.

### Fixed

- Restored managed-storage bootstrap and canonical user-memory paths for
  published runtimes.
- Hardened release automation and TestPyPI smoke sequencing during tagged
  publishes.
- Fixed the published Docker Compose healthcheck to probe `/livez`.
- Stable update checks now correctly compare prerelease local installs against
  the latest stable release.
- Removed stale production-hardening doc references and obsolete runtime config
  references from the supported release surface.

### Security

- Security reporting now directs vulnerability reports to the GitHub private
  advisory flow instead of public issues.

## [v0.2.0b10] - 2026-03-27

### Added

- No user-visible additions recorded yet.

### Changed

- Removed pre-v1 compatibility surfaces from the public backend and CLI
  contract so the published operator workflow now exposes only the canonical
  health endpoints, AWS ECS Fargate target naming, and user-memory terminology.

### Fixed

- Removed deprecated AWS App Runner aliases and legacy backend memory/profile
  compatibility shims that were no longer part of the supported release
  surface.

### Security

- No user-visible security changes recorded yet.

## [v0.2.0b9] - 2026-03-24

### Added

- No user-visible additions recorded yet.

### Changed

- Updated AWS published deploys to pull the pinned released backend image
  directly from GHCR, matching the published-runtime operator flow used on GCP.

### Fixed

- Removed the broken AWS published-runtime assumption that a released image had
  already been mirrored into ECR before ECS deploy.
- Updated AWS doctor and deploy output so published workspaces no longer report
  ECR as a required dependency when the runtime is using a published image.

### Security

- No user-visible security changes recorded yet.

## [v0.2.0b8] - 2026-03-24

### Added

- No user-visible additions recorded yet.

### Changed

- Release automation now waits for TestPyPI package index propagation before
  running the installed CLI smoke test, avoiding false negatives immediately
  after publish.

### Fixed

- Fixed managed-storage startup in published runtimes by aligning the managed
  user-memory bootstrap path with the canonical user-memory helper imports.

### Security

- No user-visible security changes recorded yet.

## [v0.2.0b7] - 2026-03-24

### Added

- No user-visible additions recorded yet.

### Changed

- Updated published workspace env defaults so released operator workspaces use
  the same current model names as `backend/.env.example`.
- Corrected published GCP deploy image resolution for GHCR-backed Artifact
  Registry remote repositories, so managed deploys target the mirrored image
  path that Cloud Run can actually pull.

### Fixed

- Restored canonical managed-storage user-memory markdown accessors so managed
  runtime bootstrap no longer crashes when the backend initializes durable
  memory artifacts.

### Security

- No user-visible security changes recorded yet.

## [v0.2.0b6] - 2026-03-23

### Added

- Added workspace-managed MCP extension support for contributor and published
  runtimes, including extension manifest management, extension doctor checks,
  and Node MCP launcher validation.
- Added backend extension loading and runtime health reporting for external MCP
  servers and custom tool contributors.

### Changed

- Reworked the backend memory/runtime model around durable markdown-based user
  memory, session memory, and updated memory APIs.
- Consolidated realtime tooling around the new user-memory tool catalog while
  preserving extension-based tool and MCP integration.
- Expanded managed deploy/operator flows across GCP, AWS, and Azure with a more
  consistent CLI surface for doctor, deploy, logs, status, and published
  workspaces.

### Fixed

- Updated the iOS profile client to read the new `/memory/user` backend route
  while preserving response decoding compatibility during rollout.
- Improved backend and CLI release/runtime docs so published workspaces,
  self-hosting, and extension prerequisites match the current implementation.

## [v0.2.0b3] - 2026-03-17

### Added

- Added a public open-source readiness checklist to define legal, security,
  onboarding, release, and cleanup gates before opening the repository.
- Added an operator-focused self-hosting guide that documents local runtime
  startup, environment setup, and managed deploy expectations.

### Changed

- Restructured the deployment orchestration internals into stage modules, which
  makes deploy behavior easier to reason about and maintain without changing
  the top-level `portworld deploy` entrypoints.
- Reorganized workspace/session ownership modules across the CLI runtime path,
  reducing import coupling and clarifying where config/session state is loaded.
- Consolidated runtime command wiring into dedicated service modules so CLI
  commands and runtime checks follow a more consistent execution path.
- Updated release documentation and automation expectations around TestPyPI,
  PyPI, and backend image publishing for tagged releases.

### Fixed

- Clarified TestPyPI installation instructions so prerelease validation uses an
  additional PyPI index for dependencies, preventing common installation
  failures during beta verification.
- Removed stale or outdated CLI planning docs that conflicted with the current
  release and workspace model.

## [v0.2.0b2] - 2026-03-16

### Changed

- Promoted the repository to prerelease `v0.2.0b2` and aligned the packaged CLI
  version metadata with the new tag.
- Improved beta release guidance so maintainers and testers use a reliable
  TestPyPI installation path instead of the incomplete default package-page
  snippet.

## [v0.2.0b1] - 2026-03-16

### Added

- First public prerelease baseline for the `portworld` CLI/operator workflow,
  including install/update and tagged release foundations used by later betas.

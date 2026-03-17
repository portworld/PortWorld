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


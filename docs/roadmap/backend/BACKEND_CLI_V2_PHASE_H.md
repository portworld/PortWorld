# Phase H Plan: PyPI Distribution + CI Releases + Terminal Bootstrap

## Summary

  Phase H turns the CLI into a real public package with automated, tag-driven releases and a reliable one-command bootstrap path.

  Locked choices:

- Installer URL now: raw GitHub script URL (until custom domain exists)
- Bootstrap behavior: conservative (auto-install pipx, but do not auto-install Python/system tools)
- Release trigger: tag-driven (vX.Y.Z)
- Prerelease lane: TestPyPI before PyPI
- Package naming policy: prefer portworld; fallback to portworld-cli if unavailable

## Implementation Changes

### 1. Release identity and package naming

- Introduce one release config source (single constants file + mirrored shell constants) with:
  - repo owner/name
  - installer script URL
  - active PyPI package name
- Keep the codebase ready for either package name (portworld or portworld-cli) without logic forks.
- Update installer and update cli guidance to use the configured package name.

### 2. PyPI-ready packaging metadata

- Extend pyproject.toml for production publishing:
  - license metadata
  - maintainer/contact metadata
  - keywords/classifiers finalized
  - project URLs finalized
- Keep dynamic version from backend.__version__ and enforce tag/version consistency in CI.
- Ensure python -m build creates valid sdist and wheel for the selected package name.

### 3. GitHub Actions release pipeline

- Add/extend workflows:
      1. cli-smoke on PR/push (already present, keep and tighten)
      2. cli-release on tag v*
- cli-release flow:
      1. Validate tag matches backend.__version__
      2. Build artifacts (sdist, wheel)
      3. Publish to TestPyPI (OIDC trusted publishing)
      4. Install from TestPyPI in a clean job and run smoke commands
      5. Publish same artifacts to PyPI (OIDC trusted publishing)
      6. Attach artifacts to GitHub Release
- No publish on main pushes.

### 4. Installer bootstrap behavior (install.sh)

- Replace hardcoded custom-domain URL with raw GitHub URL in help/output examples.
- Keep current flags and add strict behavior guarantees:
  - --version <tag|latest>
  - --no-init
  - --non-interactive
- Conservative bootstrap rules:
  - If Python missing: fail with exact platform-specific install commands (macOS/Linux), no automatic Python install.
  - If Python < 3.11: fail with upgrade instructions.
  - If pipx missing: auto-install via python3 -m pip --user pipx.
- Install source selection:
  - latest -> latest GitHub release tag
  - pinned tag -> exact tag
  - manual override env vars kept for CI/testing only
- Preserve terminal-only flow:
  - interactive TTY: install then run portworld init (unless disabled)
  - non-interactive/no TTY: install and print deterministic next steps

### 5. portworld update cli behavior

- Make update cli release-channel aware by install mode:
  - source checkout -> pipx install . --force
  - PyPI install -> pipx upgrade <package-name>
  - unknown/archive -> installer command + pinned fallback
- Keep release lookup fields in JSON output:
  - target_version
  - release_lookup_status
  - update_available
- Ensure commands no longer reference placeholder installer domains.

### 6. CLI-facing docs (not root README)

- Update:
  - backend/README.md
  - docs/BACKEND_SELF_HOSTING.md
  - docs/CLI_RELEASE_PROCESS.md
- Document:
  - official install command using raw GitHub script URL
  - PyPI install/upgrade commands
  - tag-driven release policy
  - troubleshooting for Python/pipx prerequisites
- Keep root README.md out of scope for this phase.

## Test Plan and Acceptance

  1. PR CI:

- bash -n install.sh
- portworld --help, portworld init --help, portworld providers list, portworld update cli --json
- installer smoke in --non-interactive --no-init mode

- install from TestPyPI and smoke commands
- publish to PyPI
- attach artifacts to GitHub Release

### Manual acceptance

- curl ... | bash works from a clean macOS/Linux shell with Python 3.11+
- installer failure messages are actionable when Python is missing/too old
- pipx install <package-name> and pipx upgrade <package-name> are the canonical public paths
- portworld update cli recommendations match actual release channel and package name
- no remaining public docs/reference strings point to placeholder installer domains

## Assumptions and Required External Setup

- You will create/configure PyPI and TestPyPI projects (for chosen package name).
- You will configure GitHub OIDC trusted publishing in both PyPI and TestPyPI.
- If portworld is unavailable, the active published name switches to portworld-cli and docs/installer/update messaging follow that configured name.
- Website docs can be updated after this phase; Phase H ensures terminal/install/release mechanics are correct first.

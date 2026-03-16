# Phase H Plan: PyPI Distribution + CI Releases + Terminal Bootstrap

## Summary

  Phase H turns the CLI into a real public package with automated, tag-driven releases and a reliable one-command bootstrap path.

  Locked choices:

- Installer URL now: raw GitHub script URL (until custom domain exists)
- Bootstrap behavior: `uv`-managed
- Release trigger: tag-driven (vX.Y.Z)
- Prerelease lane: TestPyPI before PyPI
- Package naming policy: prefer portworld; fallback to portworld-cli if unavailable
- Public installer package source: PyPI
- Standard public tool manager: `uv` (`pipx` remains legacy/source-checkout only)

## Implementation Changes

### 1. Release identity and package naming - done

- Introduce one release config source (single constants file + mirrored shell constants) with:
  - repo owner/name
  - installer script URL
  - active PyPI package name
- Keep the codebase ready for either package name (portworld or portworld-cli) without logic forks.
- Update installer and update cli guidance to use the configured package name.

### 2. PyPI-ready packaging metadata - done

- Extend pyproject.toml for production publishing:
  - license metadata
  - maintainer/contact metadata
  - keywords/classifiers finalized
  - project URLs finalized
- Keep dynamic version from backend.__version__ and enforce tag/version consistency in CI.
- Ensure python -m build creates valid sdist and wheel for the selected package name.

### 3. GitHub Actions release pipeline - done

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
- Implementation notes:
  - `cli-smoke` ignores `v*` tags so tag releases have a single owner.
  - TestPyPI smoke now uses `uv tool install` instead of `pipx install`.
  - GitHub Release and PyPI both reuse the same built distributions artifact.

### 4. Installer bootstrap behavior (install.sh) - done

- Replace hardcoded custom-domain URL with raw GitHub URL in help/output examples.
- Keep current flags and add strict behavior guarantees:
  - --version <tag|latest>
  - --no-init
  - --non-interactive
- Managed bootstrap rules:
  - If `uv` is missing: install it from the official Astral bootstrap script.
  - If Python is missing or < 3.11: install managed Python 3.11 with `uv`.
  - If system Python is already >= 3.11: reuse it instead of forcing a managed download.
- Install source selection:
  - latest -> latest GitHub release tag -> matching PyPI package version
  - pinned tag -> exact matching PyPI package version
  - manual override env vars kept for CI/testing only
- Preserve terminal-only flow:
  - interactive TTY: install then run portworld init (unless disabled)
  - non-interactive/no TTY: install and print deterministic next steps
- Implementation notes:
  - The shell installer no longer requires `python3` as a starting prerequisite; `bash` and `curl` are enough.
  - Internal smoke/test overrides now install through `uv tool install`, using editable mode for local directory overrides.
  - The installer still keeps `--version`, `--no-init`, and `--non-interactive` unchanged.

### 5. portworld update cli behavior - done

- Make update cli release-channel aware by install mode:
  - source checkout -> pipx install . --force
  - uv-managed public install -> uv tool upgrade <package-name>
  - legacy pipx install -> installer command + pipx upgrade fallback
  - unknown -> installer command + uv install fallback
- Keep release lookup fields in JSON output:
  - target_version
  - release_lookup_status
  - update_available
- Ensure commands no longer reference placeholder installer domains.
- Implementation notes:
  - `uv` runtime detection is based on the active interpreter path, so the CLI can distinguish `uv`-managed installs from `pipx` legacy installs.
  - This pulls part of the old “later” PyPI-first install/update story forward so the installer and `update cli` remain coherent.

### 6. CLI-facing docs (not root README) - done

- Update:
  - backend/README.md
  - docs/BACKEND_SELF_HOSTING.md
  - docs/CLI_RELEASE_PROCESS.md
- Document:
  - official install command using raw GitHub script URL
  - `uv`-managed install/upgrade commands
  - tag-driven release policy
  - troubleshooting for `uv` / Python bootstrap behavior
- Keep root README.md out of scope for this phase.
- Implementation notes:
  - Public docs now treat `uv` as the standard install/update path.
  - `pipx` remains documented only as a source-checkout developer path, not the primary public path.

## Test Plan and Acceptance

  1. PR CI:

- bash -n install.sh
- portworld --help, portworld init --help, portworld providers list, portworld update cli --json
- installer smoke in --non-interactive --no-init mode

- install from TestPyPI and smoke commands
- publish to PyPI
- attach artifacts to GitHub Release

### Manual acceptance

- curl ... | bash works from a clean macOS/Linux shell with bash + curl only
- installer bootstraps `uv` and managed Python when needed
- uv tool install <package-name> and uv tool upgrade <package-name> are the canonical public paths
- portworld update cli recommendations match actual release channel and package name
- no remaining public docs/reference strings point to placeholder installer domains

## Assumptions and Required External Setup

- You will create/configure PyPI and TestPyPI projects (for chosen package name).
- You will configure GitHub OIDC trusted publishing in both PyPI and TestPyPI.
- If portworld is unavailable, the active published name switches to portworld-cli and docs/installer/update messaging follow that configured name.
- Website docs can be updated after this phase; Phase H ensures terminal/install/release mechanics are correct first.

## Implementation Notes

- Step 4 intentionally pulled part of the old “installer default to PyPI” direction forward. Once `uv` became the bootstrap/runtime manager, keeping `pipx` as the public update path would have created an inconsistent install story.
- The public standard is now:
  - installer -> `uv` bootstrap
  - Python runtime -> system Python if >= 3.11, otherwise managed Python from `uv`
  - CLI install/update -> PyPI package via `uv tool install` / `uv tool upgrade`
- Source-checkout contributor installs remain `pipx install . --force` for now. That is a developer workflow, not the public operator path.

## Later Stages (Post-Phase H): Zero-Clone Operator Model

These stages are intentionally deferred. They extend the Phase H packaging/release baseline so a user can install and deploy without cloning this repository.

### Stage H+1. Publish backend runtime artifacts - done

- Publish a versioned backend runtime image for each CLI/backend release tag at `ghcr.io/portworld/portworld-backend:vX.Y.Z`.
- Image publishing policy:
  - immutable release tags only in H+1
  - no `latest` or `stable` moving tags yet
- Build and publish multi-arch release images for:
  - `linux/amd64`
  - `linux/arm64`
- Add provenance/signing/scan checks in CI before considering the tag release complete.
- Implementation notes:
  - The release workflow extends the existing `cli-release` tag pipeline instead of introducing a second release workflow.
  - PR/push CI now builds `backend/Dockerfile` and probes `/livez` so tag releases are not the first container validation point.
  - Release assets now include a `backend-image-manifest.json` file that records the canonical GHCR reference and pushed digest.
  - H+1 intentionally publishes the runtime artifact only; deploy/runtime mode switching remains deferred to H+2+.

### Stage H+2. Introduce CLI runtime modes - done

- Add explicit runtime source mode in CLI config:
  - `source` (repo-backed)
  - `published` (workspace-backed, zero-clone plumbing baseline)
- Keep current commands and flags; mode controls internal resolution only.
- Preserve compatibility for existing repo users by defaulting to `source` when repo markers are present.
- Implementation notes:
  - `.portworld/project.json` is now schema version `2` and persists `runtime_source`.
  - `runtime_source` is surfaced in `portworld config show` and `portworld status`, alongside `effective_runtime_source` and legacy-derivation metadata.
  - `portworld init --runtime-source ...` and `portworld config edit cloud --runtime-source ...` are now the public write paths for this setting.
  - Workspace-aware commands now accept either a source checkout or a generic workspace containing `.portworld/project.json`:
    - `config show`
    - `config edit cloud`
    - `status`
    - `logs gcp-cloud-run`
    - `doctor --target gcp-cloud-run`
  - Source-only commands now fail fast with explicit guidance when `runtime_source=published`:
    - `init`
    - `config edit providers`
    - `config edit security`
    - `doctor --target local`
    - `deploy gcp-cloud-run`
    - `update deploy`
    - `ops ...`
  - This stage adds the runtime-source plumbing only. Zero-clone workspace creation and local published-runtime execution land in H+3; managed published-image deploys remain H+4 work.

### Stage H+3. Zero-clone workspace bootstrap - done

- Extend `portworld init` for published-mode workspace bootstrap:
  - `--runtime-source published`
  - `--stack-name`
  - `--release-tag`
  - `--host-port`
- Generated published workspaces now contain:
  - root `.env`
  - root `docker-compose.yml`
  - `.portworld/project.json`
  - `.portworld/state/*`
- Published workspace defaults:
  - target path: `~/.portworld/stacks/<name>` unless `--project-root` is passed
  - release pin: exact tag, defaulting to `v{backend.__version__}`
  - image ref: `ghcr.io/portworld/portworld-backend:vX.Y.Z`
- Local published-mode support now works without a repo checkout for:
  - `status`
  - `doctor --target local`
  - `ops check-config`
  - `ops bootstrap-storage`
  - `ops migrate-storage-layout`
  - `ops export-memory`
- Implementation notes:
  - `.portworld/project.json` is now schema version `3` and persists `deploy.published_runtime` metadata: `release_tag`, `image_ref`, and `host_port`.
  - `status` now reports pinned published-runtime metadata and local Compose/container health when `runtime_source=published`.
  - Published local doctor/ops commands execute through `docker compose` one-off runs against the generated workspace files instead of repo-backed backend paths.
  - Managed published-mode deploys are still intentionally deferred. `deploy gcp-cloud-run` and `update deploy` remain source-only until H+4.

### Stage H+4. Cloud deploy from published image - done

- `portworld deploy gcp-cloud-run` and `portworld update deploy` now support `runtime_source=published`.
- Deploy strategy now branches by runtime source:
  - `source` mode keeps the existing Cloud Build + standard Artifact Registry flow
  - `published` mode deploys the workspace's pinned published release without a repo checkout or Cloud Build
- Published managed deploys use an Artifact Registry remote repository that fronts GHCR rather than deploying directly from `ghcr.io/...`.
- Version movement stays explicit:
  - published workspaces keep an immutable pinned release tag
  - `update deploy` redeploys the current pin only
  - changing versions still happens through the workspace pin (`init --runtime-source published --release-tag ...`)
- Implementation notes:
  - No new top-level commands were added; the existing deploy/update command surface now branches internally by `runtime_source`.
  - `--tag` remains valid for source-mode deploys only and now fails clearly in published mode.
  - Deploy state and `status` now record whether the managed service came from a repo build or a published release, including the pinned release tag and canonical published image ref when applicable.
  - Published-mode deploys derive a separate Artifact Registry remote repository name from the configured source-build repository (`<artifact_repository>-ghcr`) so source and published image paths do not collide.

### Stage H+5. Installer default to PyPI + managed mode onboarding

- Keep `curl ... | bash` as entrypoint.
- Install CLI from PyPI (Phase H baseline), then offer:
  - repo-backed flow (advanced/contributor)
  - managed zero-clone flow (default for operators)
- Add clear recovery/diagnostic commands for both flows.

### Stage H+6. Documentation and support policy

- Split docs explicitly into:
  - operator quickstart (zero-clone, published artifacts)
  - contributor quickstart (clone + source mode)
- Define support matrix and lifecycle policy:
  - supported OS/Python versions
  - supported CLI-to-backend version skew window
  - deprecation policy for legacy source-only assumptions

# Backend CLI Implementation Plan

## Summary

This document records the current v1, backend-embedded CLI implementation plan and completion state.
For the forward-looking public CLI product direction, see [BACKEND_CLI_PRODUCT_DIRECTION.md](./BACKEND_CLI_PRODUCT_DIRECTION.md).

This document turns the backend CLI specification into an implementation backlog with explicit sequencing, code ownership boundaries, public interfaces, and acceptance criteria.

The public goal is to ship an installable `portworld` CLI that:

- initializes backend configuration through a guided wizard
- validates local and Cloud Run readiness
- exposes the current backend operator tasks under a cleaner command surface
- deploys the backend to GCP Cloud Run through an opinionated guided workflow

The implementation is split across three owned areas:

- CLI package and command surface
- backend config and storage contracts
- docs and migration surfaces

The critical dependency is managed storage support. `deploy gcp-cloud-run` cannot be completed until the backend supports the managed storage contract defined in `docs/BACKEND_CLI_SPEC.md`.

## Current Status

- implemented through Task 14
- CLI v1 implementation is complete for the scope of this plan
- available today:
  - `portworld init`
  - `portworld doctor --target local`
  - `portworld doctor --target gcp-cloud-run`
  - `portworld deploy gcp-cloud-run`
  - `portworld ops check-config`
  - `portworld ops bootstrap-storage`
  - `portworld ops export-memory`
  - `portworld ops migrate-storage-layout`
- important implementation note:
  - editable install has been unreliable in the current local environment; packaged install and wheel build have been the validated paths
  - managed storage now selects through a shared backend contract and supports Postgres metadata plus GCS artifact persistence
  - verified Cloud Run deploys use `/livez` for public liveness checks; `/healthz` remains a compatibility alias

## Documentation Authority

The source of truth for CLI implementation status is:

- `docs/roadmap/backend/BACKEND_CLI_IMPLEMENTATION_PLAN.md`
- `docs/roadmap/backend/BACKEND_CLI_SPEC.md`

User-facing docs such as `backend/README.md` and `docs/BACKEND_SELF_HOSTING.md` still contain legacy operator-first flows and should not be treated as the implementation authority for CLI status.

## Ownership Boundaries

### 1. CLI package and command surface

Owned paths:

- `pyproject.toml`
- `backend/cli.py`
- `portworld_cli/`

Responsibilities:

- public `portworld` command
- Click command tree
- repo-root detection
- env file read/write helpers
- CLI metadata under `.portworld/`
- human-readable and JSON output formatting
- shell adapters for Docker and `gcloud`

### 2. Backend config and storage contracts

Owned paths:

- `backend/core/`
- `backend/bootstrap/`
- `backend/core/storage.py`
- `backend/infrastructure/storage/`

Responsibilities:

- configuration loading and validation
- managed-storage env contract
- storage backend selection
- Postgres metadata persistence
- GCS artifact persistence
- parity for export/reset/bootstrap behavior

### 3. Docs and migration surfaces

Owned paths:

- `backend/README.md`
- `docs/BACKEND_SELF_HOSTING.md`
- `docs/BACKEND_CLI_SPEC.md`
- `docs/roadmap/backend/`

Responsibilities:

- installation guidance
- local quick-start
- Cloud Run quick-start
- migration guidance from `python -m backend.cli`

## Delivery Order

Implementation should land in this order:

1. package the public CLI - complete
2. add shared CLI infrastructure - complete
3. add env parsing and env writing - complete
4. migrate current operator commands under `portworld ops` - complete
5. implement `portworld init` - complete
6. implement `portworld doctor` for local mode - complete
7. add the GCP adapter layer - complete
8. implement `doctor --target gcp-cloud-run` - complete
9. add the backend managed-storage contract - complete
10. implement managed storage backends - complete
11. implement `deploy gcp-cloud-run` - complete
12. update docs and migration guidance - complete

This order avoids building deploy orchestration on top of an unstable CLI surface or on top of a storage model that Cloud Run cannot use durably.

## Concrete Backlog

## Task 1: Package the public CLI

`Status: Complete`

### Owner area

- `pyproject.toml`
- `portworld_cli/`
- `backend/cli.py`

### Deliverables

- add installable packaging with console script `portworld`
- create the root Click app
- define global CLI options:
  - `--project-root`
  - `--verbose`
  - `--json`
  - `--non-interactive`
  - `--yes`
- keep `python -m backend.cli` functional as a legacy/internal path

### Acceptance criteria

- built package exposes `portworld --help`
- legacy `python -m backend.cli ...` commands still run
- editable install caveat is documented for environments where hidden editable `.pth` files are skipped

### Implementation notes

- `pyproject.toml` now defines the installable `portworld` console script
- `portworld_cli/` contains the public Click CLI scaffold and root options
- `python -m backend.cli` remains intact as the legacy/internal operator path
- `backend/Dockerfile` installs from the packaged project

## Task 2: Add shared CLI infrastructure

`Status: Complete`

### Owner area

- `portworld_cli/context.py`
- `portworld_cli/output.py`
- `portworld_cli/paths.py`
- `portworld_cli/state.py`

### Deliverables

- repo-root detection by walking up from CWD
- required marker detection for:
  - `backend/Dockerfile`
  - `backend/.env.example`
  - `docker-compose.yml`
- shared CLI context object
- shared command result model for human and JSON output
- `.portworld/state/gcp-cloud-run.json` read/write helpers

### Acceptance criteria

- commands resolve repo root consistently
- commands fail clearly when repo root cannot be found
- JSON output is emitted consistently across commands that support it

### Implementation notes

- lazy repo-root detection is implemented via shared path helpers
- shared human/JSON result rendering is implemented in the CLI layer
- `.portworld/state` helpers exist for future deploy metadata
- current stubs and implemented commands use the shared output/context model

## Task 3: Implement env parsing and env writing

`Status: Complete`

### Owner area

- `portworld_cli/envfile.py`
- `backend/.env.example`

### Deliverables

- `.env` parser for known PortWorld keys
- canonical `.env` writer using the ordering from `.env.example`
- timestamped backup creation before rewrite
- preservation of unknown env vars under a `Custom overrides` section
- shared defaults accessor so `init`, `doctor`, and deploy derive values from one place

### Acceptance criteria

- rewriting `backend/.env` is stable and deterministic
- backup files are created as `backend/.env.bak.<unix_ms>`
- unknown keys survive rewrite

### Implementation notes

- `portworld_cli/envfile.py` is the canonical env template/parser/writer module
- legacy aliases such as `MISTRAL_API_KEY` and `MISTRAL_BASE_URL` are read and preserved
- unknown keys are retained under a `Custom overrides` section on rewrite
- canonical writes always rewrite the full file in template order

## Task 4: Move operator commands under `portworld ops`

`Status: Complete`

### Owner area

- `portworld_cli/commands/ops.py`
- `backend/cli.py`
- `backend/bootstrap/runtime.py`
- `backend/bootstrap/memory_export.py`

### Deliverables

- expose these commands under `portworld ops`:
  - `check-config`
  - `bootstrap-storage`
  - `export-memory`
  - `migrate-storage-layout`
- keep these wrappers thin over current backend behavior
- switch default output to human-readable summaries with `--json` for raw structured output

### Acceptance criteria

- every current operator task is reachable through `portworld ops ...`
- existing backend functionality is preserved
- JSON output remains available for automation

### Implementation notes

- `portworld ops ...` is implemented as direct Python wrappers around backend functions
- the public ops commands do not shell out to `python -m backend.cli`
- the legacy argparse CLI remains available in parallel
- human-readable output is now the default, with `--json` retaining automation-friendly payloads

## Task 5: Implement `portworld init`

`Status: Complete`

### Owner area

- `portworld_cli/commands/init.py`
- `portworld_cli/envfile.py`

### Deliverables

- guided wizard that writes `backend/.env`
- prompt flow for:
  - OpenAI key
  - visual memory enablement and vision key
  - tooling enablement and Tavily key
  - optional local bearer-token generation
- support for non-interactive flags:
  - `--with-vision`
  - `--without-vision`
  - `--with-tooling`
  - `--without-tooling`
  - `--openai-api-key`
  - `--vision-provider-api-key`
  - `--tavily-api-key`
- reuse current values as defaults on rerun
- create backup before overwriting an existing env file

### Acceptance criteria

- first-run wizard produces a working `backend/.env`
- rerun updates the file safely
- non-interactive mode fails when required values are missing
- success output points the user to `portworld doctor` and deploy/local next steps

### Implementation notes

- `portworld init` is implemented with interactive and non-interactive flows
- reruns rewrite `backend/.env` canonically and create `.env.bak.<unix_ms>`
- existing values are reused as defaults on rerun
- enabling tooling without a Tavily key succeeds but reports a warning because `web_search` remains unavailable

## Task 6: Implement `portworld doctor` for local mode

`Status: Complete`

### Owner area

- `portworld_cli/commands/doctor.py`
- `backend/bootstrap/runtime.py`

### Deliverables

- local-mode diagnostic checks for:
  - repo root
  - `backend/.env`
  - Docker installation
  - Docker Compose availability
  - backend config validity
  - provider config validity for enabled features
  - storage bootstrap probe when `--full` is used
- `PASS`, `WARN`, and `FAIL` output model
- JSON schema for diagnostics

### Acceptance criteria

- `doctor` clearly reports missing repo markers, Docker, `.env`, or config issues
- warnings do not produce a non-zero exit code
- `--json` produces a complete machine-readable report

### Implementation notes

- local `portworld doctor` is implemented with staged `PASS` / `WARN` / `FAIL` checks
- `--full` runs a storage bootstrap probe
- `--target gcp-cloud-run` is present only as a forward-compatible not-implemented placeholder for Task 8
- `doctor` uses repo-local env loading and backend Python validators directly

## Task 7: Add the GCP adapter layer

`Status: Complete`

### Owner area

- `portworld_cli/gcp/`

### Deliverables

- shell adapters for:
  - `gcloud` auth/account checks
  - project and region selection
  - API enablement
  - service-account creation
  - Artifact Registry management
  - Cloud Build submission
  - Secret Manager operations
  - Cloud SQL operations
  - GCS bucket operations
- structured result types for success/failure per adapter

### Acceptance criteria

- GCP helpers can be used independently by `doctor` and deploy
- failures are returned in a structured form suitable for human and JSON summaries

### Implementation notes

- Added `portworld_cli/gcp/` as the shared adapter package for `gcloud` execution and typed GCP helper results
- Landed read-only and mutating helper surfaces now so Task 8 and Task 13 can reuse the same adapter contract
- Kept CLI prompts, summaries, and `.portworld/state` handling outside the adapter layer

## Task 8: Implement `doctor --target gcp-cloud-run`

`Status: Complete`

### Owner area

- `portworld_cli/commands/doctor.py`
- `portworld_cli/gcp/`

### Deliverables

- GCP-specific readiness checks for:
  - `gcloud` installed
  - authenticated account present
  - project selected or provided
  - region selected or provided
  - required APIs enabled or enable-able
  - deployable image name derivation
  - production-posture compatibility of local backend config

### Acceptance criteria

- users can validate GCP prerequisites before deploy
- failures include exact remediation steps
- no resources are provisioned by the doctor command

### Implementation notes

- Replaced the stubbed GCP doctor path with real read-only checks using the Task 7 adapters
- Added checks for auth, project, region, required APIs, deployable image naming, local secret readiness, and production-posture compatibility
- Kept the command non-provisioning: it inspects GCP state but does not enable APIs, create resources, or write CLI state

## Task 9: Add the managed-storage contract to the backend

`Status: Complete`

### Owner area

- `backend/core/settings.py`
- `backend/bootstrap/runtime.py`
- `backend/core/storage.py`

### Deliverables

- add the managed-storage env contract:
  - `BACKEND_STORAGE_BACKEND=local|postgres_gcs`
  - `BACKEND_DATABASE_URL`
  - `BACKEND_OBJECT_STORE_PROVIDER=filesystem|gcs`
  - `BACKEND_OBJECT_STORE_BUCKET`
  - `BACKEND_OBJECT_STORE_PREFIX`
- keep local SQLite/filesystem mode as the default
- add validation rules so the backend can load either local or managed mode

### Acceptance criteria

- backend starts in local mode without regression
- backend validates managed mode without ambiguous config errors

### Implementation notes

- Added the managed-storage env contract to `Settings` and the active `backend/.env.example`
- Added explicit runtime validation for `local` versus `postgres_gcs` storage configuration
- Managed mode is now recognized and validated through the shared backend contract used by runtime and CLI flows

## Task 10: Implement managed storage backend selection

`Status: Complete`

### Owner area

- `backend/infrastructure/storage/`
- `backend/core/storage.py`
- `backend/bootstrap/memory_export.py`

### Deliverables

- split storage responsibilities into interfaces that can support:
  - local SQLite + filesystem
  - managed Postgres + GCS
- preserve export/reset/bootstrap semantics behind the abstraction
- select storage backend based on the new env contract

### Acceptance criteria

- the backend can boot with either storage backend through one functional contract
- local behavior remains unchanged from a user perspective

### Implementation notes

- Storage backend selection now routes `local` and `postgres_gcs` through one shared contract
- Runtime, doctor, and ops flows consume backend-agnostic storage info instead of assuming local filesystem paths
- Capability-specific managed-mode behavior is handled behind the shared storage contract rather than a top-level not-implemented guard

## Task 11: Implement Postgres metadata storage

`Status: Complete`

### Owner area

- `backend/infrastructure/storage/postgres*.py`
- `backend/bootstrap/runtime.py`

### Deliverables

- Postgres-backed implementations for the metadata/index responsibilities currently handled by SQLite
- automated bootstrap for schema creation sufficient for first deploys
- compatibility with session index, artifact index, and vision frame index behaviors needed by runtime and memory admin flows

### Acceptance criteria

- metadata operations needed by runtime, export, reset, and retention work in managed mode
- schema bootstrap is automated enough for the Cloud Run path

### Implementation notes

- Managed mode now bootstraps Postgres schema automatically during storage bootstrap
- Session index, artifact index, vision frame index, profile documents, session memory documents, and event-log metadata are stored in Postgres
- Managed runtime, retention, reset eligibility, and memory-admin flows now use Postgres-backed metadata operations

## Task 12: Implement GCS artifact storage

`Status: Complete`

### Owner area

- `backend/infrastructure/storage/gcs*.py`
- `backend/bootstrap/memory_export.py`

### Deliverables

- GCS-backed artifact/blob persistence for managed mode
- export support that preserves the current logical manifest shape
- parity for profile and session artifact persistence

### Acceptance criteria

- profile/session artifacts persist to GCS in managed mode
- export works with GCS-backed artifacts

### Implementation notes

- Managed profile/session artifacts, event logs, and raw vision-frame blobs now persist through a provider-neutral object-store contract with a GCS implementation
- Managed reads prefer GCS artifacts and retain Postgres fallbacks for compatibility with previously stored managed data
- Memory export is now storage-agnostic and works in managed mode through artifact byte loaders rather than local filesystem paths

## Task 13: Implement `portworld deploy gcp-cloud-run`

`Status: Complete`

### Owner area

- `portworld_cli/commands/deploy.py`
- `portworld_cli/gcp/`
- `portworld_cli/state.py`

### Deliverables

- full deploy orchestration matching `docs/BACKEND_CLI_SPEC.md`
- deploy stages:
  1. repo and config discovery
  2. prerequisite validation
  3. parameter collection
  4. API enablement
  5. service-account setup
  6. Artifact Registry setup
  7. Cloud Build image build
  8. Secret Manager setup
  9. Cloud SQL setup
  10. GCS bucket setup
  11. runtime config assembly
  12. Cloud Run deploy
  13. post-deploy validation and summary
- persist deploy metadata under `.portworld/state/gcp-cloud-run.json`

### Acceptance criteria

- first deploy provisions required GCP resources and deploys successfully
- repeat deploy reuses compatible resources
- partial failures report what was created and how to rerun safely
- human and JSON outputs include service URL, image, resources, and next steps

### Implementation notes

- `portworld deploy gcp-cloud-run` is implemented through a dedicated deploy runtime that resolves parameters from flags, current `gcloud` config, and `.portworld/state/gcp-cloud-run.json`
- the deploy path provisions or reuses Artifact Registry, Secret Manager, Cloud SQL, GCS, and Cloud Run resources through the `gcloud`-backed adapter layer
- verified Cloud Run deploys assemble `BACKEND_DATABASE_URL` with the Cloud SQL Unix socket mount rather than a public-IP connection string
- deploy summaries and post-deploy probes now use `/livez` for public liveness and authenticated `/readyz` for readiness

## Task 14: Update docs and migration guidance

`Status: Complete`

### Owner area

- `backend/README.md`
- `docs/BACKEND_SELF_HOSTING.md`
- `docs/BACKEND_CLI_SPEC.md`
- `docs/roadmap/backend/`

### Deliverables

- make `portworld` the primary documented install/use path
- add local quick-start using `portworld init`
- add Cloud Run quick-start using `portworld deploy gcp-cloud-run`
- document migration from `python -m backend.cli` to `portworld ops ...`

### Acceptance criteria

- docs reflect the new CLI-first onboarding path
- local Docker self-hosting remains documented as the default simple route

### Implementation notes

- `backend/README.md` now treats `portworld` as the primary operator CLI surface for init, doctor, deploy, and `ops` workflows
- `docs/BACKEND_SELF_HOSTING.md` now documents the CLI-first local path and the migration mapping from `python -m backend.cli` operator commands to `portworld ops ...`
- backend-facing docs now document `/livez` as the public liveness endpoint for deployed services; `/healthz` remains available as a compatibility alias

## Public Interfaces To Land

### CLI surface

- `portworld init`
- `portworld doctor`
- `portworld deploy gcp-cloud-run`
- `portworld ops check-config`
- `portworld ops bootstrap-storage`
- `portworld ops export-memory`
- `portworld ops migrate-storage-layout`

### Global options

- `--project-root`
- `--verbose`
- `--json`
- `--non-interactive`
- `--yes`

### Managed-storage env contract

- `BACKEND_STORAGE_BACKEND=local|postgres_gcs`
- `BACKEND_DATABASE_URL`
- `BACKEND_OBJECT_STORE_PROVIDER=filesystem|gcs`
- `BACKEND_OBJECT_STORE_BUCKET`
- `BACKEND_OBJECT_STORE_PREFIX`

## Test Matrix

### Packaging

- `portworld --help` works after built package install
- legacy `python -m backend.cli` still works during migration
- editable install behavior is treated as an environment-specific caveat until it is explicitly fixed

### Init

- creates `backend/.env` from scratch
- rewrites existing `backend/.env` with backup creation
- preserves unknown keys
- fails cleanly in non-interactive mode when required values are missing

### Doctor

- local mode catches missing repo markers, Docker, `.env`, and invalid provider config
- GCP mode catches missing `gcloud`, auth, project, or production-incompatible config
- `--json` returns stable structured output

### Managed storage

- local storage path still boots and behaves as before
- managed mode supports bootstrap, export, reset, and retention-required flows

### Deploy

- fresh deploy provisions required GCP resources
- repeat deploy reuses compatible resources
- partial failure reports created resources and safe rerun guidance
- final output includes URL, image, core resources, and next steps

## Assumptions And Defaults

- the public CLI implementation now lives under the top-level `portworld_cli/` package
- `click` is the CLI framework for v1
- `backend/.env` remains the runtime source of truth
- `.portworld/` stores only CLI metadata and should be gitignored
- Cloud Run deploys are public at the network layer and protected by backend bearer-token auth in production mode
- Cloud Run deploy is blocked on managed storage support in the backend
- tests may be added for CLI and storage work because this is a new platform surface with deployment-critical behavior

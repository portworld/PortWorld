# PortWorld Backend CLI Specification

## Summary

This document describes the current v1 CLI contract that was implemented inside the backend codebase.
For the next-stage public CLI product direction, see [BACKEND_CLI_PRODUCT_DIRECTION.md](./BACKEND_CLI_PRODUCT_DIRECTION.md).

This document specifies CLI v1 for the PortWorld backend.

It is the implementation companion to [BACKEND_PLATFORM_ROADMAP.md](./BACKEND_PLATFORM_ROADMAP.md) and defines the first real public CLI surface for deploying and operating the backend.

The CLI exists to solve one problem first:

- make PortWorld backend easy to configure, validate, and deploy

CLI v1 is intentionally narrow and opinionated. It is not a generic DevOps framework and it is not yet a general extension toolkit. It is a deploy-and-ops CLI for a Docker-first backend.

The first public command is:

- `portworld`

The first distribution target is:

- `pipx`

The first guided managed deployment target is:

- GCP Cloud Run

The first Cloud Run deployment path is based on:

- Cloud Build
- Artifact Registry
- Secret Manager
- Cloud SQL Postgres
- Google Cloud Storage

## Implementation Status

This specification is still the design authority for CLI v1, and implementation has now progressed through Task 14.

Available today:

- packaging and public `portworld` CLI scaffold
- shared CLI infrastructure for repo detection, output, and state helpers
- canonical `backend/.env` parsing and writing
- `portworld ops ...`
- `portworld init`
- `portworld doctor --target local`
- GCP adapter layer under `portworld_cli/gcp/`
- `portworld doctor --target gcp-cloud-run`
- `portworld deploy gcp-cloud-run`
- managed-storage env parsing and validation contract
- managed storage backend selection
- managed Postgres metadata persistence
- managed GCS artifact persistence
- deploy state reuse through `.portworld/state/gcp-cloud-run.json`

No remaining CLI v1 implementation gaps from this spec are intentionally left pending. Later follow-up items are listed in the final section of this document.

Current implementation notes:

- `ops` and `doctor` call backend Python functions directly rather than shelling out to `python -m backend.cli`
- the public `doctor` GCP path is implemented as a read-only preflight check; provisioning remains part of `deploy gcp-cloud-run`
- managed mode now boots through the shared storage contract and supports Postgres metadata plus GCS-backed artifact persistence
- verified Cloud Run deploys use `/livez` for public liveness and authenticated `/readyz` for readiness; `/healthz` remains a compatibility alias
- the CLI package exists, but editable-install behavior has been unreliable in the current local environment; packaged install and wheel build are the validated paths so far
- legacy `python -m backend.cli` remains supported during migration

## Documentation Authority

Until the broader docs migration is complete, the authoritative CLI implementation-tracking docs are:

- `docs/roadmap/backend/BACKEND_CLI_IMPLEMENTATION_PLAN.md`
- `docs/roadmap/backend/BACKEND_CLI_SPEC.md`

User-facing docs such as `backend/README.md` and `docs/BACKEND_SELF_HOSTING.md` still contain legacy operator-first flows and should not be treated as the current implementation authority for CLI status.

## Goals

CLI v1 must let a technical user:

- initialize local backend configuration through a guided wizard
- validate local prerequisites and backend configuration without reverse-engineering environment variables
- deploy the backend to Cloud Run through an opinionated guided flow
- run the existing backend operator actions through a cleaner public command surface

CLI v1 should reduce the amount of manual reading, shell scripting, and cloud-console work required to get a working deployment.

## Non-Goals

CLI v1 does not try to:

- support all cloud providers at parity
- replace Docker as the canonical packaging layer
- become a general infrastructure-as-code engine
- introduce a new primary runtime config format for the backend
- solve the full storage redesign inside the CLI itself
- provide full rollback, teardown, and fleet management from day one
- become the plugin system for providers or tools

## Audience And Operating Model

### Primary audience

CLI v1 targets:

- developers working from a checked-out PortWorld repo
- technical operators self-hosting or deploying the backend
- developers who want a guided path from source checkout to working backend

### Repo-aware first

CLI v1 is repo-aware first.

The CLI assumes the user is usually running from a PortWorld repository checkout containing at least:

- `backend/Dockerfile`
- `backend/.env.example`
- `docker-compose.yml`

The CLI should auto-detect the repo root from the current working directory by walking upward until it finds the required markers.

The CLI must also support an explicit override:

- `--project-root PATH`

If the repo root cannot be detected and `--project-root` is not supplied, the CLI should fail with a clear error.

### Installation model

The public installation path is:

```bash
pipx install portworld
```

CLI v1 should be packaged as a Python console script entrypoint named `portworld`.

## Current State Constraints

The current backend already includes:

- a Python operator CLI at `python -m backend.cli`
- Docker packaging via `backend/Dockerfile`
- local self-hosting via `docker-compose.yml`
- environment-based runtime configuration centered on `backend/.env`
- configuration checks and storage bootstrap helpers in backend Python code

CLI v1 should reuse these foundations rather than replace them wholesale.

The current backend now includes:

- packaging metadata for an installable CLI
- a user-facing local config wizard
- local readiness diagnostics through `portworld doctor --target local`
- a public `ops` namespace for the existing operator tasks

The current backend now includes:

- managed cloud deployment orchestration for GCP Cloud Run
- managed storage support for Cloud Run via Postgres + GCS
- real `doctor --target gcp-cloud-run` checks

The spec therefore includes both:

- user-facing CLI behavior
- backend implementation dependencies required to make that behavior real

## CLI Architecture

### Implementation approach

CLI v1 should be implemented as a new user-facing CLI layer built on `click`.

Reasoning:

- `click` is already present in backend dependencies
- it supports nested commands, prompts, confirmations, and clean option handling
- it is a better fit than raw `argparse` for interactive setup and deployment flows

The current `backend.cli` module should remain as a lower-level operator implementation layer during migration.

### Internal layering

The CLI should be split conceptually into four layers:

1. command surface
2. repo/config discovery
3. backend/runtime adapters
4. external tool/cloud adapters

The command surface owns:

- UX
- prompts
- summaries
- exit codes
- JSON output formatting

Repo/config discovery owns:

- repo-root detection
- reading and writing `backend/.env`
- reading and writing CLI metadata files

Backend/runtime adapters own:

- wrapping existing config validation and storage bootstrap logic
- mapping CLI decisions into backend runtime configuration

External tool/cloud adapters own:

- shelling out to `docker`
- shelling out to `gcloud`
- shelling out to `python -m backend.cli` where reuse is pragmatic in v1

### External command strategy

For v1, the CLI should prefer shelling out to stable external CLIs when they already model the workflow well:

- `docker`
- `docker compose`
- `gcloud`

The CLI should not reimplement the entire GCP control plane through raw APIs in v1.

This keeps the implementation smaller and makes auth/debugging behavior easier to understand.

## Global UX Conventions

### Command behavior

Commands should be:

- interactive by default
- scriptable through flags
- machine-readable when `--json` is supported

### Global options

The CLI should support these common global options:

- `--project-root PATH`
- `--verbose`
- `--json`
- `--non-interactive`
- `--yes`

`--yes` only suppresses confirmation prompts. It does not invent missing required values.

`--non-interactive` means the command must fail if required values are missing.

### Output model

Default output should be concise and human-readable.

JSON output should be supported for:

- `doctor`
- `deploy gcp-cloud-run`
- `ops check-config`
- `ops bootstrap-storage`
- `ops export-memory`
- `ops migrate-storage-layout`

`init` may support `--json`, but human output is the primary experience.

### Exit codes

- `0`: success
- `1`: runtime or validation failure
- `2`: usage error or missing required non-interactive input

### Status levels

Human-readable diagnostic output should distinguish:

- `PASS`
- `WARN`
- `FAIL`

Warnings should not produce a non-zero exit code unless the command is explicitly running in a strict mode.

## Persistent State Model

### Runtime source of truth

The backend runtime source of truth remains:

- `backend/.env`

CLI v1 must not introduce a new primary runtime configuration format.

### Wizard behavior

`portworld init` is a wizard-led experience, but the result must still be a concrete editable `backend/.env` file.

This gives the user:

- simple guided setup
- compatibility with the existing backend runtime model
- transparency for manual inspection and overrides

### CLI metadata

The CLI may maintain deployment metadata under:

- `.portworld/`

This metadata is not authoritative runtime configuration.
It exists only to improve repeat deploys and operator ergonomics.

The initial metadata path should be:

- `.portworld/state/gcp-cloud-run.json`

This file may store:

- last used project id
- last used region
- Cloud Run service name
- Artifact Registry repository name
- Cloud SQL instance name
- GCS bucket name
- deploy timestamp

The spec assumes `.portworld/` should be gitignored.

## Command Tree

CLI v1 command tree:

```text
portworld init
portworld doctor
portworld deploy gcp-cloud-run
portworld ops check-config
portworld ops bootstrap-storage
portworld ops export-memory
portworld ops migrate-storage-layout
```

## Command Specification

## `portworld init`

### Status

Implemented.

### Purpose

Initialize or refresh the local backend runtime configuration through a guided wizard.

### Primary outcomes

- create `backend/.env` when missing
- update `backend/.env` when present
- fill strong defaults for non-sensitive and non-essential values
- request only the minimum high-signal inputs from the user

### Prompt flow

The wizard should ask for:

1. realtime provider choice
   - v1 default and only supported value at implementation start: `openai`
2. OpenAI API key
3. whether to enable visual memory
4. if visual memory is enabled:
   - vision provider choice
   - v1 default and only supported value at implementation start: `mistral`
   - vision provider API key
5. whether to enable realtime tooling
6. if realtime tooling is enabled:
   - web search provider choice
   - v1 default and only supported value at implementation start: `tavily`
   - Tavily API key
7. whether to generate a local bearer token
   - default: no for local-only development

The wizard should not ask the user to make decisions about advanced tuning values unless the command is later extended with an advanced mode.

### Defaults written by `init`

Unless explicitly changed by the user, `init` should write strong defaults for:

- `REALTIME_PROVIDER=openai`
- `OPENAI_REALTIME_MODEL=gpt-realtime`
- `OPENAI_REALTIME_VOICE=ash`
- `VISION_MEMORY_ENABLED=false`
- `REALTIME_TOOLING_ENABLED=false`
- `VISION_MEMORY_PROVIDER=mistral`
- `VISION_MEMORY_MODEL=ministral-3b-2512`
- `BACKEND_PROFILE=development`
- `HOST=0.0.0.0`
- `PORT=8080`
- `LOG_LEVEL=INFO`
- all current rate-limit and tuning defaults from `backend/.env.example`

### Existing file behavior

If `backend/.env` does not exist:

- create it from canonical defaults plus wizard answers

If `backend/.env` already exists:

- parse current values
- use current values as prompt defaults where applicable
- rewrite the file in canonical PortWorld order
- create a timestamped backup before overwriting

Backup path format:

- `backend/.env.bak.<unix_ms>`

Unknown environment variables should be preserved when practical and appended under a `Custom overrides` section on rewrite.

### Flags

`init` should support:

- `--force`
- `--non-interactive`
- `--with-vision`
- `--without-vision`
- `--with-tooling`
- `--without-tooling`
- `--openai-api-key VALUE`
- `--vision-provider-api-key VALUE`
- `--tavily-api-key VALUE`
- `--json`

### Non-interactive behavior

In non-interactive mode, all required values must be provided via existing env state or explicit flags.
Missing required values should fail with exit code `2`.

### Success output

Human output should summarize:

- file written
- enabled features
- missing optional integrations, if any
- suggested next commands:
  - `portworld doctor`
  - `docker compose up --build`
  - `portworld deploy gcp-cloud-run`

### JSON output

Suggested shape:

```json
{
  "ok": true,
  "command": "init",
  "project_root": "/abs/path/to/repo",
  "env_path": "/abs/path/to/repo/backend/.env",
  "backup_path": "/abs/path/to/repo/backend/.env.bak.1770000000000",
  "features": {
    "vision_memory": false,
    "realtime_tooling": true,
    "web_search_provider": "tavily"
  }
}
```

## `portworld doctor`

### Status

Implemented.

- implemented today: `--target local`, `--target gcp-cloud-run`, `--full`, JSON output, staged PASS/WARN/FAIL diagnostics

### Purpose

Run high-level diagnostics for the current repo and optional deployment target.

### Relationship to existing checks

`doctor` is the public diagnostic command.
It should wrap existing backend configuration checks and add:

- repo validation
- external CLI availability checks
- deployment target checks
- user-facing summaries

### Default behavior

Without a target, `doctor` should validate local development and self-hosting readiness.

### Supported targets

- `local`
- `gcp-cloud-run`

Default target:

- `local`

### Checks for `local`

`doctor --target local` checks:

- repo root detected
- `backend/.env` exists
- Docker installed
- Docker Compose available
- backend config parses successfully
- provider config is valid for enabled features
- storage bootstrap probe succeeds when `--full` is passed

### Checks for `gcp-cloud-run`

`doctor --target gcp-cloud-run` checks:

- repo root detected
- `backend/.env` exists
- `gcloud` installed
- authenticated account available
- active or selected GCP project available
- required APIs enabled or enable-able
- deployable image path can be constructed
- required secrets are present locally or will need creation
- local config is compatible with production posture expectations

### Flags

`doctor` supports:

- `--target local|gcp-cloud-run`
- `--full`
- `--project PROJECT_ID`
- `--region REGION`
- `--json`

### Output contract

Human output should present:

- overall status
- check rows with `PASS` / `WARN` / `FAIL`
- exact remediation steps for failures

JSON output shape:

```json
{
  "ok": false,
  "command": "doctor",
  "target": "gcp-cloud-run",
  "project_root": "/abs/path/to/repo",
  "checks": [
    {
      "id": "gcloud_installed",
      "status": "pass",
      "message": "gcloud is installed"
    },
    {
      "id": "backend_env_exists",
      "status": "fail",
      "message": "backend/.env is missing",
      "action": "Run 'portworld init' first"
    }
  ]
}
```

## `portworld deploy gcp-cloud-run`

### Status

Implemented.

### Purpose

Provision and deploy PortWorld backend to Cloud Run through a guided official path.

### Design goal

This command should feel close to a one-command deploy while still being explicit about major choices and prerequisites.

It should be a guided orchestrator, not just a wrapper that prints `gcloud` commands for the user to run manually.

### Deployment model

The first deploy path uses:

- repo source as build input
- Cloud Build to build the image
- Artifact Registry to store the image
- Cloud Run to serve the backend
- Secret Manager for sensitive runtime values
- Cloud SQL Postgres for metadata and index persistence
- GCS for artifact/blob persistence

### Default service settings

Unless overridden, deploy should use:

- service name: `portworld-backend`
- Artifact Registry repository: `portworld`
- Cloud SQL instance name: `portworld-pg`
- database name: `portworld`
- bucket prefix: `portworld`
- request timeout: `3600s`
- cpu: `1`
- memory: `1Gi`
- min instances: `1`
- max instances: `10`
- concurrency: `10`
- ingress: external
- allow unauthenticated: true

### Authentication and traffic model

Cloud Run should be deployed as publicly reachable at the network layer by default.
Application-level protection should come from the backend itself.

Therefore the deploy command must ensure production runtime posture includes:

- `BACKEND_PROFILE=production`
- explicit `BACKEND_BEARER_TOKEN`
- explicit `CORS_ORIGINS`
- explicit `BACKEND_ALLOWED_HOSTS`

This keeps the service usable by mobile/websocket clients without requiring Cloud Run IAM tokens in the client.

### Deploy stages

The command must execute the following stages in order.

#### Stage 1: repo and config discovery

- locate repo root
- require `backend/.env`
- load local config
- derive feature set from current config

If `backend/.env` is missing, fail with:

- `Run 'portworld init' first`

#### Stage 2: prerequisite validation

- verify `gcloud` installed
- verify authenticated account
- verify billing-enabled GCP project is selected or provided
- verify Dockerfile exists

#### Stage 3: deployment parameters

Collect or confirm:

- project id
- region
- Cloud Run service name
- Artifact Registry repository name
- Cloud SQL instance name
- database name
- bucket name
- production CORS origins
- allowed hosts

Prompt for missing values in interactive mode.

Suggested default region:

- `us-central1`

#### Stage 4: API enablement

Ensure these APIs are enabled:

- Cloud Run API
- Cloud Build API
- Artifact Registry API
- Secret Manager API
- Cloud SQL Admin API
- Cloud Storage API

The command may enable them automatically after confirmation.

#### Stage 5: service account setup

Create or verify a dedicated runtime service account.

Default name:

- `<service-name>-runtime`

Required runtime permissions:

- `roles/secretmanager.secretAccessor`
- `roles/cloudsql.client`
- `roles/storage.objectAdmin` on the selected GCS bucket

#### Stage 6: Artifact Registry setup

Create or verify the Artifact Registry repository.

Default repository format:

- Docker repository named `portworld`

#### Stage 7: image build and publish

Build and publish the backend image through Cloud Build.

Recommended image tag format:

- `gcr.io` is not used
- use Artifact Registry fully qualified path
- tag with git SHA when available, else timestamp

Image naming pattern:

- `<region>-docker.pkg.dev/<project>/<repo>/portworld-backend:<tag>`

In v1, the CLI should invoke:

- `gcloud builds submit`

rather than directly calling the Cloud Build API.

#### Stage 8: Secret Manager setup

Sensitive values must be stored in Secret Manager.

Required secrets depend on enabled features but may include:

- `openai-api-key`
- `vision-provider-api-key`
- `tavily-api-key`
- `backend-bearer-token`
- `backend-database-url`

Secret names should be prefixed by service name.

Naming pattern:

- `<service-name>-<secret-name>`

If the bearer token is missing locally, the CLI should generate a secure token and store it.

#### Stage 9: Cloud SQL Postgres setup

Create or verify:

- Cloud SQL Postgres instance
- database `portworld`
- application user for backend access

The CLI should assemble a database URL and store it in Secret Manager.

V1 should use password-based app credentials stored as a secret.

#### Stage 10: GCS bucket setup

Create or verify a GCS bucket for backend artifacts.

Default bucket naming pattern:

- `<project-id>-portworld-artifacts`

If the exact default name is unavailable, the CLI should prompt for an alternative.

#### Stage 11: runtime config assembly

The CLI should assemble the final Cloud Run runtime configuration by combining:

- local non-sensitive defaults and feature toggles from `backend/.env`
- production overrides required for managed deployment
- secret references from Secret Manager
- managed-storage env vars

#### Stage 12: Cloud Run deploy

Deploy the service with:

- built image
- runtime service account
- Cloud SQL connectivity
- secret references
- non-secret env vars
- production posture overrides
- timeout/concurrency/resource defaults

#### Stage 13: post-deploy validation

After deploy, print:

- Cloud Run service URL
- liveness check command
- readiness check command
- next operator commands
- which secrets and resources were used

The CLI optionally runs a final liveness probe.

For public Cloud Run validation, the liveness endpoint should be:

- `GET /livez`

`GET /healthz` remains available as a compatibility alias, but `/livez` is the public probe target because it has been validated against the default Cloud Run service URL.

### Flags

`deploy gcp-cloud-run` supports:

- `--project PROJECT_ID`
- `--region REGION`
- `--service SERVICE_NAME`
- `--artifact-repo NAME`
- `--sql-instance NAME`
- `--database NAME`
- `--bucket NAME`
- `--cors-origins CSV`
- `--allowed-hosts CSV`
- `--tag TAG`
- `--min-instances N`
- `--max-instances N`
- `--concurrency N`
- `--cpu CPU`
- `--memory MEMORY`
- `--json`
- `--non-interactive`
- `--yes`

### Non-interactive behavior

In non-interactive mode, the command must fail if any required deploy parameter is missing.
It must not silently invent project, region, host, or CORS settings.

### Idempotency expectations

Repeated deploys should:

- reuse existing resources when compatible
- update secret values when requested or when local values changed
- build and deploy a new image
- update `.portworld/state/gcp-cloud-run.json`

The command should not recreate Cloud SQL or buckets if they already exist and are compatible.

### Human success output

The final summary should include:

- deployment target and project
- region
- Cloud Run service name
- service URL
- Artifact Registry image URL
- Cloud SQL instance name
- bucket name
- enabled backend features
- suggested follow-up commands

### JSON success output

Suggested shape:

```json
{
  "ok": true,
  "command": "deploy gcp-cloud-run",
  "project_id": "my-project",
  "region": "us-central1",
  "service_name": "portworld-backend",
  "service_url": "https://portworld-backend-abc-uc.a.run.app",
  "image": "us-central1-docker.pkg.dev/my-project/portworld/portworld-backend:abc123",
  "resources": {
    "artifact_registry_repository": "portworld",
    "cloud_sql_instance": "portworld-pg",
    "database_name": "portworld",
    "bucket_name": "my-project-portworld-artifacts",
    "service_account": "portworld-backend-runtime@my-project.iam.gserviceaccount.com"
  },
  "features": {
    "vision_memory": true,
    "realtime_tooling": true,
    "web_search_provider": "tavily"
  },
  "next_steps": [
    "curl https://portworld-backend-abc-uc.a.run.app/livez",
    "Run 'portworld doctor --target gcp-cloud-run --project my-project --region us-central1'"
  ]
}
```

## `portworld ops check-config`

### Status

Implemented.

### Purpose

Expose the existing backend configuration check as a stable CLI subcommand under the `ops` namespace.

### Behavior

This command is a close wrapper around the current backend config check implementation.

Flags:

- `--full-readiness`
- `--json`

Default output should be human-readable with an option for raw JSON.

## `portworld ops bootstrap-storage`

### Status

Implemented.

### Purpose

Bootstrap storage for local-mode development or self-hosting.

### Behavior

This is a wrapper around the current storage bootstrap logic.

Flags:

- `--json`

For v1, this command remains local-storage focused.

## `portworld ops export-memory`

### Status

Implemented.

### Purpose

Export current backend memory artifacts to a zip file.

### Flags

- `--output PATH`
- `--json`

This command maps to the current backend memory export implementation.

## `portworld ops migrate-storage-layout`

### Status

Implemented.

### Purpose

Run the current legacy storage migration helper.

### Flags

- `--json`

## Configuration Mapping Rules

### Local config source

Local deployment and local diagnostics use `backend/.env` directly.

### Managed deploy config source

Cloud Run deploy starts from `backend/.env`, then applies production-specific overrides.

### Production overrides applied by deploy

The deploy command must force:

- `BACKEND_PROFILE=production`
- explicit `BACKEND_BEARER_TOKEN`
- explicit `CORS_ORIGINS`
- explicit `BACKEND_ALLOWED_HOSTS`
- explicit managed-storage settings

### Sensitive vs non-sensitive values

Sensitive values must go to Secret Manager.

Non-sensitive values should be passed as normal environment variables.

#### Sensitive values

- `OPENAI_API_KEY`
- `VISION_PROVIDER_API_KEY`
- `TAVILY_API_KEY`
- `BACKEND_BEARER_TOKEN`
- `BACKEND_DATABASE_URL`

#### Non-sensitive values

- `REALTIME_PROVIDER`
- `OPENAI_REALTIME_MODEL`
- `OPENAI_REALTIME_VOICE`
- `VISION_MEMORY_ENABLED`
- `REALTIME_TOOLING_ENABLED`
- `VISION_MEMORY_PROVIDER`
- `VISION_MEMORY_MODEL`
- `CORS_ORIGINS`
- `BACKEND_ALLOWED_HOSTS`
- rate-limit and tuning values
- managed storage bucket and provider identifiers

## Managed Storage Contract Required For CLI v1

Cloud Run deployment requires backend support for managed persistence.
That support does not fully exist yet, so CLI v1 implementation depends on backend follow-up work.

### Required backend runtime contract

The backend must support a managed storage mode alongside the current local mode.

Minimum required env contract:

- `BACKEND_STORAGE_BACKEND=local|postgres_gcs`
- `BACKEND_DATABASE_URL=`
- `BACKEND_OBJECT_STORE_PROVIDER=filesystem|gcs`
- `BACKEND_OBJECT_STORE_BUCKET=`
- `BACKEND_OBJECT_STORE_PREFIX=`

For local self-hosting, the current storage envs remain valid.

For Cloud Run deploys, the CLI should set:

- `BACKEND_STORAGE_BACKEND=postgres_gcs`
- `BACKEND_OBJECT_STORE_PROVIDER=gcs`
- `BACKEND_OBJECT_STORE_BUCKET=<bucket>`
- `BACKEND_OBJECT_STORE_PREFIX=<service-name>`
- `BACKEND_DATABASE_URL` from Secret Manager

### Storage parity requirements

Managed storage must preserve the functional behaviors needed by CLI v1:

- bootstrap-able persistent storage
- profile persistence
- session artifact persistence
- memory export
- session reset
- retention logic parity where practical

The CLI spec does not define the internal storage implementation beyond this contract.

## Failure Handling

### General rules

Every failure should say:

- what failed
- at which stage it failed
- whether the failure is blocking or advisory
- what the user should do next

### Examples of blocking failures

- repo root not found
- `backend/.env` missing for commands that require config
- `gcloud` not installed for Cloud Run deploy
- authenticated GCP account missing
- Cloud Build submission failed
- Cloud SQL instance creation failed
- secret creation or access failed
- Cloud Run deploy failed

### Partial failure handling

If a deploy fails after some resources were created, the CLI should:

- report which resources now exist
- report which stage failed
- avoid attempting destructive rollback automatically in v1
- tell the user how to rerun safely

## Security Requirements

CLI v1 must treat secrets carefully.

Requirements:

- never print raw secret values in normal output
- redact secrets in verbose logs where possible
- avoid storing secrets in `.portworld/` metadata
- use Secret Manager by default for Cloud Run secrets
- generate a strong bearer token when one is needed and absent

## Documentation Requirements

When CLI v1 lands, it must update or create companion docs for:

- installation of `portworld`
- local quick-start using `portworld init`
- Cloud Run deployment quick-start
- migration path from `python -m backend.cli` to `portworld ops ...`

## Acceptance Criteria

CLI v1 is complete when:

- `pipx install portworld` provides a working `portworld` command
- `portworld init` can create a usable `backend/.env` from a wizard
- `portworld doctor` correctly diagnoses local and Cloud Run readiness
- `portworld deploy gcp-cloud-run` can build, provision, and deploy through the official path
- the deploy path uses Secret Manager, Cloud SQL Postgres, and GCS as specified
- the command outputs are understandable to humans and available in JSON for automation
- existing operator actions remain accessible under `portworld ops ...`

## Implementation Sequence

Completed:

1. package the installable CLI and establish command scaffolding
2. implement repo-root detection and env file loading/writing
3. implement canonical env parsing and writing
4. migrate existing raw operator commands into `portworld ops ...`
5. implement `portworld init`
6. implement `portworld doctor` for local mode
7. implement GCP adapter helpers for `gcloud`, Cloud Build, and Artifact Registry
8. implement real `doctor --target gcp-cloud-run`
9. implement managed storage backend selection
10. implement managed Postgres metadata and GCS artifact support
11. implement Secret Manager integration
12. implement Cloud SQL Postgres and GCS provisioning helpers
13. implement `deploy gcp-cloud-run`
14. connect final deploy output to `.portworld/state/gcp-cloud-run.json`
15. finish docs and migration guidance

## Open Follow-Up Items After CLI v1

These items are intentionally left for later documents or phases:

- deploy support for Fly.io and Railway
- standalone binary distribution
- tool/provider scaffolding commands
- richer rollback and teardown support
- advanced environment profiles beyond the first production path
- broader provider-aware deploy templates

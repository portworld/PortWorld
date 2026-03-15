# PortWorld CLI v2 Implementation Plan

## Summary

This document is the execution plan for the next public CLI phase after the current backend-embedded v1 implementation.

It is intentionally additive.
The current v1 CLI remains the implemented baseline, and this plan defines the next sequence of work needed to evolve `portworld` into the primary public setup, deploy, and lifecycle surface for the PortWorld framework.

This plan does not re-decide product shape.
The source-of-truth design inputs are:

- [BACKEND_CLI_PRODUCT_DIRECTION.md](./BACKEND_CLI_PRODUCT_DIRECTION.md)
- [BACKEND_CLI_COMMAND_CONTRACT.md](./BACKEND_CLI_COMMAND_CONTRACT.md)
- [BACKEND_CLI_IMPLEMENTATION_PLAN.md](./BACKEND_CLI_IMPLEMENTATION_PLAN.md)
- [BACKEND_CLI_SPEC.md](./BACKEND_CLI_SPEC.md)

Default choices locked for this plan:

- the first implementation slice is the config foundation
- the CLI stays in `backend/cli_app` until later extraction
- `status` and `logs` start from `.portworld/state` plus current GCP adapters
- installer and distribution work follow the config foundation and core command expansion

## Current Starting Point

The implemented v1 CLI already provides:

- `portworld init`
- `portworld doctor --target local`
- `portworld doctor --target gcp-cloud-run`
- `portworld deploy gcp-cloud-run`
- `portworld ops ...`

The next CLI phase should add:

- a higher-level project config model
- public inspection and lifecycle commands
- section-based config editing
- provider discovery
- a real installer/distribution path
- a later extraction of the public CLI out of `backend/`

## Phase A: Config Foundation

`Status: Complete`

### Goal

Introduce a stable CLI-owned project configuration layer without breaking the current runtime compatibility model.

### Deliverables

- add `.portworld/project.json` as the high-level non-secret project config
- add CLI-side config loading and writing abstractions
- define how `backend/.env` is generated or refreshed from project config
- keep `.portworld/state/*.json` as deploy metadata only
- teach current commands to read the project config when relevant without breaking current behavior

### Required behavior

- `init` becomes the primary writer of `.portworld/project.json`
- `deploy` can resolve defaults from project config before falling back to prompts
- future `status` and `config` commands treat project config as the high-level source
- `backend/.env` remains runtime-compatible output for local/backend flows

### Acceptance criteria

- `.portworld/project.json` can be created, read, and updated safely
- current v1 commands still function after the config layer lands
- the ownership split is explicit:
  - `.portworld/project.json` for non-secret project choices
  - `.portworld/state/*.json` for deploy metadata
  - `backend/.env` for generated/runtime compatibility output

### Implementation notes

- Added `backend/cli_app/project_config.py` as the schema-versioned config layer for `.portworld/project.json`.
- Phase A schema is intentionally minimal and high-level:
  - providers: realtime, vision, tooling
  - security: backend profile, CORS origins, allowed hosts
  - deploy defaults: preferred target plus `gcp_cloud_run` defaults
- `portworld init` is now the Phase A writer for `.portworld/project.json` and still rewrites `backend/.env` canonically.
- When `.portworld/project.json` is missing, `init` derives it from the current `backend/.env` plus remembered deploy state before writing it.
- `backend/.env` remains the runtime-compatible artifact and secret input source:
  - secrets stay in `backend/.env`
  - advanced runtime tuning stays in `backend/.env`
  - unknown custom overrides are preserved on rewrite
- `.portworld/state/gcp-cloud-run.json` remains deploy metadata only and is still written by deploy, not by init or doctor.
- Phase A command precedence is now:
  - explicit CLI flags
  - `.portworld/project.json`
  - existing local/machine state such as `gcloud` config
  - remembered deploy state where applicable
  - hard defaults
- Current Phase A command behavior:
  - `init` reads/writes project config and refreshes `backend/.env`
  - `deploy gcp-cloud-run` reads project config for deploy defaults and still writes deploy state only
  - `doctor --target gcp-cloud-run` reads project config for project/region fallback
  - local doctor and `ops` remain env-driven for compatibility

## Phase B: Setup And Config UX Expansion

`Status: Complete`

### Goal

Expand setup beyond the current narrow init flow and let users edit one configuration area at a time.

### Deliverables

- expand `portworld init` toward the staged sections defined in the command contract
- add `portworld config show`
- add `portworld config edit providers`
- add `portworld config edit security`
- add `portworld config edit cloud`

### Required behavior

- `init` remains the full guided flow
- `config edit ...` reruns only the requested section
- config edits update project config and refresh generated runtime-compatible artifacts as needed
- no secrets move into `.portworld/project.json`

### Acceptance criteria

- a user can inspect current config without opening raw files
- a user can change providers, security settings, or cloud defaults without repeating the full setup flow
- current init behavior remains compatible for users who still only want local setup

### Implementation notes

- Added a shared config UX/runtime layer in `backend/cli_app/config_runtime.py` so `init` and `config edit ...` use the same section logic and env/project-config sync path.
- Added the public `portworld config` surface:
  - `portworld config show`
  - `portworld config edit providers`
  - `portworld config edit security`
  - `portworld config edit cloud`
- `config show` now:
  - reads `.portworld/project.json` when present
  - derives config from `backend/.env` plus deploy state when the project config file is missing
  - reports non-secret secret-readiness status from `backend/.env`
  - supports JSON output with the effective project config, env path, and `derived_from_legacy` state
- `config edit providers` now owns:
  - provider feature toggles for vision and tooling
  - provider-related credentials in `backend/.env`
  - preserving provider choices in `.portworld/project.json`
- `config edit security` now owns:
  - backend profile
  - CORS origins
  - allowed hosts
  - local bearer-token generation, replacement, and clearing in `backend/.env`
- `config edit cloud` now owns:
  - `project_mode`
  - `cloud_provider`
  - `deploy.preferred_target`
  - GCP Cloud Run defaults under `deploy.gcp_cloud_run`
- Phase B minimally expanded the project-config shape with `cloud_provider` while keeping `schema_version` at `1`.
- `init` is now a staged full-project setup path built from the same provider, security, and cloud section workflows.
- `init` still preserves the compatibility-era flags:
  - `--with-vision|--without-vision`
  - `--with-tooling|--without-tooling`
  - provider credential flags
- `init` also now accepts security and cloud-default flags so the full setup can run non-interactively.
- `backend/.env` remains the generated runtime artifact:
  - secrets remain env-only
  - advanced runtime tuning remains env-only
  - unknown custom overrides continue to survive canonical rewrites
- Switching a project back to local mode clears the active cloud target fields at the top level but preserves stored GCP Cloud Run defaults under `deploy.gcp_cloud_run`.

## Phase C: Inspection Commands

### Goal

Add public read-oriented commands for deployed-project inspection before adding heavier lifecycle behavior.

Status: done

### Deliverables

- add `portworld status`
- add `portworld logs gcp-cloud-run`

### Required behavior

- `status` reads `.portworld/state` first
- `status` supplements with live provider data only when enough context exists
- `logs gcp-cloud-run` is a stable public wrapper over the current GCP logging path
- neither command invents a new remote-management control plane

### Acceptance criteria

- a user can inspect the last known deploy target and service URL without re-running deploy
- a user can retrieve managed logs through a public command shape
- `status` supports JSON output with structured deploy and health summary data

### Implementation notes

- Added a shared read-only inspection layer in `backend/cli_app` so `status` and `logs` reuse the same project-root, project-config, env, and remembered deploy-state resolution path.
- Extracted deploy metadata handling into a shared `DeployState` module instead of keeping it embedded only in deploy runtime code.
- `status` is state-first and opportunistically enriches from live Cloud Run only when project, region, and service name are all resolvable.
- Live `status` enrichment uses the existing Cloud Run service describe adapter plus short `/livez` and `/readyz` HTTP probes.
- Live inspection failures do not fail `status`; they are surfaced as warning data while preserving the last-known deploy summary.
- Added a new GCP logging adapter backed by `gcloud logging read`, and exposed it publicly through `portworld logs gcp-cloud-run`.
- `logs gcp-cloud-run` is fetch-only in this phase:
  - no follow/tail mode yet
  - context resolves from explicit flags first, then remembered deploy state, then project config
- `status` JSON now includes:
  - project mode and cloud provider
  - active target
  - last-known deploy summary
  - live service summary when available
  - health probe results
  - non-secret secret-readiness booleans
- Phase C stayed additive:
  - existing `init`, `doctor`, `deploy`, `config`, and `ops` behavior remains intact
  - no command-tree redesign
  - no mutation of `.portworld/project.json`, `.portworld/state/*.json`, or `backend/.env` from the new inspection commands

## Phase D: Provider Discovery And Lifecycle Commands

### Goal

Make supported providers and update workflows visible through the public CLI.

Status: done

### Deliverables

- add `portworld providers list`
- add `portworld providers show <provider>`
- add `portworld update cli`
- add `portworld update deploy`

### Required behavior

- provider commands are informational and read-only first
- `update cli` covers the public CLI installation path
- `update deploy` focuses on the current managed deploy path rather than inventing a provider-agnostic release manager
- command behavior must stay compatible with the command contract and current v1 surfaces

### Acceptance criteria

- supported cloud and model/tool providers are discoverable from the CLI
- users can understand provider requirements without digging through multiple docs
- users have a documented public path for CLI updates and managed redeploy/update behavior

### Implementation notes

- Added a CLI-owned provider catalog for the current official runtime surface only:
  - `gcp`
  - `openai`
  - `mistral`
  - `tavily`
- `portworld providers list` is read-only and repo-independent.
- `portworld providers show <provider>` supports `gcp-cloud-run` as an alias into the GCP provider entry.
- Provider output is grounded in the current runtime and CLI surface rather than future roadmap placeholders.
- `update cli` landed as guidance-first in this phase:
  - reports the current CLI version
  - detects repo-checkout installs conservatively
  - falls back cleanly when install-mode detection is unknown
  - prints recommended upgrade commands instead of mutating the installation
- `update deploy` landed as a narrow public redeploy wrapper over the existing managed deploy flow:
  - resolves the active managed target from remembered state or project config
  - currently supports only the `gcp-cloud-run` path
  - reuses the existing deploy runtime and flag surface rather than forking deploy logic
- Phase D stayed additive:
  - existing `deploy gcp-cloud-run` remains the explicit provider-target deploy command
  - no command-tree redesign
  - no change to config/state/env ownership boundaries from earlier phases

## Phase E: Installer And Distribution

### Goal

Provide a real public installation path without moving setup logic into shell scripts.

Status: done

### Deliverables

- polish the public `pipx` install path
- add a thin `install.sh`
- define and implement installer prerequisite checks
- hand off from the installer into `portworld init`

### Required behavior

- the installer remains thin
- the installer validates only baseline machine requirements directly:
  - `bash`
  - `curl`
  - `python3`
- provider-specific tooling checks remain in the CLI and only run when required by user flow
- shell bootstrap does not duplicate provider, deploy, or config logic

### Acceptance criteria

- a new user can install the CLI through a public path without repo-local dev knowledge
- installer output clearly hands off to `portworld init`
- public install docs match the actual supported install/update path

### Implementation notes

- Added a thin root-level `install.sh` as the first public installer surface.
- The installer remains intentionally narrow:
  - checks baseline machine requirements only
  - bootstraps `pipx` when missing
  - installs the CLI
  - hands off into `portworld init`
- Phase E uses a GitHub source archive install path instead of a `git+https` install, so `git` is not a baseline installer prerequisite.
- The public manual fallback path is now:
  - `python3 -m pipx install --force "https://github.com/armapidus/PortWorld/archive/refs/heads/main.zip"`
- The public shell bootstrap path is now:
  - `curl -fsSL https://openclaw.ai/install.sh | bash`
- `install.sh` attempts to auto-run `portworld init` when an interactive terminal is available and falls back to an explicit next-step message when it is not.
- `update cli` was aligned with the new public install story:
  - source-checkout installs still recommend `pipx install . --force`
  - non-repo installs now recommend rerunning the installer first
  - archive-based `pipx` install remains the manual fallback
- Updated the public backend install docs so they no longer treat repo-local `pipx install .` as the primary user-facing install path.

## Phase F: Extraction From `backend/`

### Goal

Move the public CLI into a clearer package boundary after the command and config model stabilize.

### Deliverables

- extract the public CLI into a top-level package boundary
- keep backend runtime code in `backend/`
- preserve the public command surface during migration

### Required behavior

- extraction is an implementation re-home, not a redesign
- command behavior and compatibility guarantees remain intact
- backend runtime and public CLI boundaries become clearer

### Acceptance criteria

- the public CLI no longer reads as backend-internal code
- the command surface remains stable across the extraction
- current users do not need to relearn command names due only to internal repo movement

## Public Interfaces And Ownership

The implementation plan must protect these high-level interfaces:

### Project config

- `.portworld/project.json`
- non-secret project choices only
- owned by `init`, `config`, `deploy`, and `status`

### Deploy metadata

- `.portworld/state/*.json`
- deploy metadata only
- owned by `deploy`, `status`, `logs`, and future `update deploy`

### Runtime compatibility output

- `backend/.env`
- generated/runtime compatibility artifact
- refreshed by `init` and future `config edit ...` flows

### Public command additions

- `portworld status`
- `portworld logs gcp-cloud-run`
- `portworld config show`
- `portworld config edit providers|security|cloud`
- `portworld providers list|show`
- `portworld update cli|deploy`

## Compatibility Rules

This plan must preserve the compatibility guarantees from the command contract.

### Required compatibility

- no clean-break renames in this phase
- current working v1 commands remain valid
- new commands are additive
- repo extraction does not justify command-surface churn

### Migration expectations

- `portworld init`, `doctor`, `deploy gcp-cloud-run`, and `ops ...` remain valid
- new top-level commands become preferred where they add new user-facing value
- no deprecation policy is assumed unless documented in a later dedicated migration plan

## Test Plan

The implementation plan should require acceptance checks by phase, not only one final checklist.

### Phase A

- current v1 commands still work after config foundation lands
- `.portworld/project.json` can be created and read safely
- `backend/.env` generation remains compatible with current backend runtime expectations

### Phase B

- `init` can create or update project config and generated runtime config together
- `config edit ...` updates only one section without forcing a full setup rerun
- config commands do not write secrets into `.portworld/project.json`

### Phase C

- `status` works from `.portworld/state` without requiring live provider queries
- `logs gcp-cloud-run` works through the current GCP adapter layer
- `status` emits stable JSON output
- `status` and `logs` are now implemented and wired into the public CLI

### Phase D

- `providers` commands are read-only and informative
- `update cli` and `update deploy` have clear human output and explicit failure guidance
- `providers` and `update` are now implemented and wired into the public CLI

### Phase E

- installer bootstraps the CLI without duplicating setup logic
- installer prerequisites are checked clearly
- install path hands off to `portworld init`
- installer and install/update docs are now implemented and aligned with the public archive-based install path

### Phase F

- extraction preserves command behavior
- packaging/install entrypoints still resolve correctly after the move

## Assumptions

- the product direction and command contract are already accepted and should not be renegotiated inside this plan
- GCP remains the only fully concrete managed provider during this implementation plan
- installer and distribution work are important, but not the first implementation slice
- `backend/cli_app` remains the implementation base until later extraction
- `status` and `logs` should begin as pragmatic inspection commands, not a full remote-control plane

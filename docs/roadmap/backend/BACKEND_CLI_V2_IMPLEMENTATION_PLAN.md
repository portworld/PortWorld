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

## Phase B: Setup And Config UX Expansion

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

## Phase C: Inspection Commands

### Goal

Add public read-oriented commands for deployed-project inspection before adding heavier lifecycle behavior.

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

## Phase D: Provider Discovery And Lifecycle Commands

### Goal

Make supported providers and update workflows visible through the public CLI.

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

## Phase E: Installer And Distribution

### Goal

Provide a real public installation path without moving setup logic into shell scripts.

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

### Phase D

- `providers` commands are read-only and informative
- `update cli` and `update deploy` have clear human output and explicit failure guidance

### Phase E

- installer bootstraps the CLI without duplicating setup logic
- installer prerequisites are checked clearly
- install path hands off to `portworld init`

### Phase F

- extraction preserves command behavior
- packaging/install entrypoints still resolve correctly after the move

## Assumptions

- the product direction and command contract are already accepted and should not be renegotiated inside this plan
- GCP remains the only fully concrete managed provider during this implementation plan
- installer and distribution work are important, but not the first implementation slice
- `backend/cli_app` remains the implementation base until later extraction
- `status` and `logs` should begin as pragmatic inspection commands, not a full remote-control plane

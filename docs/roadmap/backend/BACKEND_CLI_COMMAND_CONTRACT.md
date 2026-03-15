# PortWorld CLI Command Contract

## Summary

This document defines the next public command contract for `portworld`.

It is the source of truth for:

- the public command tree
- command intent and audience
- interactive and non-interactive behavior
- human and JSON output expectations
- compatibility rules for the current v1 CLI surface

This document is forward-looking, but it is intentionally closer to implementation than the product-direction doc.

Related docs:

- [BACKEND_CLI_PRODUCT_DIRECTION.md](./BACKEND_CLI_PRODUCT_DIRECTION.md)
- [BACKEND_CLI_IMPLEMENTATION_PLAN.md](./BACKEND_CLI_IMPLEMENTATION_PLAN.md)
- [BACKEND_CLI_SPEC.md](./BACKEND_CLI_SPEC.md)

## Relationship To Current v1 Docs

The current v1 docs remain the record of what was implemented inside the backend-embedded CLI.

This command-contract doc does not replace those v1 docs.
Instead, it defines the intended public command model for the next CLI phase so that a later implementation plan does not need to re-decide:

- command names
- command hierarchy
- compatibility behavior
- output conventions
- interaction rules

## Command Design Principles

### 1. Public commands first

The public CLI should be understandable to a new user without knowing backend internals.

The main user-facing commands should cover:

- setup
- validation
- deployment
- inspection
- updates

### 2. Wizard first, operator second

The default experience should be guided and friendly.

Lower-level or maintenance-oriented behavior should remain under `portworld ops`.

### 3. Provider-generic top level

Top-level commands should be stable across providers.

Provider specificity belongs in:

- subcommands such as `deploy gcp-cloud-run`
- targets such as `doctor --target gcp-cloud-run`
- provider-aware views such as `logs gcp-cloud-run`

### 4. Compatibility before cleanup

The current working v1 commands remain supported during the next phase.

New public surfaces are additive unless a later deprecation plan is explicitly documented.

### 5. Human output by default

Commands should default to concise human-readable output.

Structured JSON output remains required for commands that return machine-consumable status, diagnostics, or deployment state.

## Public Command Tree

The preferred public command tree is:

- `portworld init`
- `portworld doctor`
- `portworld deploy`
- `portworld status`
- `portworld logs`
- `portworld config`
- `portworld providers`
- `portworld update`
- `portworld ops`

### Concrete near-term subcommands

The next contract assumes these concrete command shapes:

- `portworld deploy gcp-cloud-run`
- `portworld doctor --target local`
- `portworld doctor --target gcp-cloud-run`
- `portworld logs gcp-cloud-run`
- `portworld config show`
- `portworld config edit providers`
- `portworld config edit security`
- `portworld config edit cloud`
- `portworld providers list`
- `portworld providers show <provider>`
- `portworld update cli`
- `portworld update deploy`
- `portworld ops check-config`
- `portworld ops bootstrap-storage`
- `portworld ops export-memory`
- `portworld ops migrate-storage-layout`

## Command-By-Command Contract

### `portworld init`

Audience:

- first-time users
- users reconfiguring a project

Purpose:

- create or update project configuration through a guided setup flow
- generate local runtime compatibility output such as `backend/.env`
- optionally transition directly into readiness checking or deploy

Interaction model:

- interactive by default
- wizard-led
- sectioned and resumable in future phases

Near-term expectations:

- preserve the current local setup path
- grow toward project-mode, provider, tool, security, and cloud sections

### `portworld doctor`

Audience:

- all users

Purpose:

- validate the current project state
- validate provider requirements
- validate deploy readiness without mutating resources

Interaction model:

- non-mutating
- should not require confirmation prompts
- should be safe to run repeatedly

Current and near-term targets:

- `local`
- `gcp-cloud-run`

### `portworld deploy`

Audience:

- users deploying to managed infrastructure

Purpose:

- orchestrate managed deployment workflows
- provision or reuse required resources
- publish deploy summaries and follow-up commands

Interaction model:

- guided by default
- prompts only for missing deploy-critical values
- non-interactive mode must fail rather than invent missing values

Concrete near-term target:

- `gcp-cloud-run`

### `portworld status`

Audience:

- users with an existing configured or deployed project

Purpose:

- show the current project mode
- show the latest deploy target and service URL
- show relevant health and readiness summary

Interaction model:

- read-only
- should be safe and fast

Near-term expectation:

- should read `.portworld/state/*` first
- should optionally query the active managed target when enough context exists

### `portworld logs`

Audience:

- users debugging a deployed service

Purpose:

- expose provider-aware logs in a stable CLI shape

Interaction model:

- read-only
- provider-specific underneath, provider-generic at the command surface

Concrete near-term target:

- `gcp-cloud-run`

### `portworld config`

Audience:

- users editing project choices after setup

Purpose:

- inspect current project configuration
- rerun only one configuration section
- avoid forcing users back through the full setup wizard

Concrete near-term shapes:

- `portworld config show`
- `portworld config edit providers`
- `portworld config edit security`
- `portworld config edit cloud`

### `portworld providers`

Audience:

- users comparing or understanding supported providers

Purpose:

- list supported cloud providers
- list supported model/tool providers
- show requirements, capabilities, and setup notes

Concrete near-term shapes:

- `portworld providers list`
- `portworld providers show <provider>`

### `portworld update`

Audience:

- users maintaining the CLI or deployed backend over time

Purpose:

- update the local CLI installation
- update the deployed backend version or image

Concrete near-term shapes:

- `portworld update cli`
- `portworld update deploy`

### `portworld ops`

Audience:

- advanced users and maintainers

Purpose:

- expose lower-level backend maintenance tasks
- preserve a stable place for operator-oriented commands that should not dominate the beginner UX

Behavior:

- stays thin over backend/runtime functionality where appropriate
- is not the first command family new users should learn

## Global Flags And Output Rules

The shared global flags remain:

- `--project-root`
- `--verbose`
- `--json`
- `--non-interactive`
- `--yes`

### Global behavior

`--project-root`

- explicitly selects the PortWorld project root

`--verbose`

- enables more detailed operational output
- should never be required for a user to understand the basic result

`--json`

- emits machine-readable output
- required for status-like and deploy-like commands that return structured data

`--non-interactive`

- fails when required input is missing
- does not invent or silently default high-impact missing values

`--yes`

- accepts confirmations
- does not answer required prompts that materially affect setup or deployment

### Output expectations

Human output should:

- summarize the outcome clearly
- show remediation or next steps when needed
- avoid exposing secrets

JSON output should:

- preserve stable top-level command metadata
- expose checks, resources, next steps, and status in structured form when relevant

## Interactive Behavior Rules

### Setup commands

`init` and future `config edit ...` commands are interactive by default.

They should:

- guide the user through missing decisions
- reuse current values as defaults when possible
- write or regenerate runtime-compatible config artifacts

### Diagnostic commands

`doctor`, `status`, and `logs` should avoid mutation and avoid confirmations.

They may fail with clear instructions when required context is missing.

### Deploy commands

`deploy` should:

- prompt only for missing deployment-critical values
- explain material mutations before execution
- give a final summary with validation and follow-up commands

## Provider-Target Naming Rules

The command contract is provider-generic, but the first concrete managed provider remains GCP.

### Naming rule

Managed provider-specific deploy and logs commands should follow:

- `deploy <provider-target>`
- `logs <provider-target>`

Readiness targets should follow:

- `doctor --target <provider-target>`

### Concrete initial target

The only fully concrete managed target in this contract is:

- `gcp-cloud-run`

Future providers should follow the same naming rule rather than inventing unrelated command shapes.

## Config And State Touchpoints

This document does not define the full schema, but it constrains command ownership at a high level.

### Project config

Intended high-level home:

- `.portworld/project.json`

Commands expected to read or write it:

- `init`
- `config show`
- `config edit ...`
- `deploy`
- `status`

### Deploy metadata

Intended high-level home:

- `.portworld/state/*.json`

Commands expected to read or write it:

- `deploy`
- `status`
- `logs`
- `update deploy`

### Runtime compatibility output

Current high-level generated artifact:

- `backend/.env`

Commands expected to generate or refresh it:

- `init`
- future `config edit ...` flows

## Compatibility And Migration Rules

The current v1 command surface remains supported as compatibility behavior.

### Compatibility policy

- no clean-break renames in the next phase
- current working commands stay valid
- new commands are additive unless later deprecation is explicitly documented

### Compatibility table

| Current working command | Preferred contract position | Compatibility status |
|---|---|---|
| `portworld init` | unchanged | keep |
| `portworld doctor` | unchanged | keep |
| `portworld deploy gcp-cloud-run` | unchanged under `deploy` | keep |
| `portworld ops check-config` | unchanged under `ops` | keep |
| `portworld ops bootstrap-storage` | unchanged under `ops` | keep |
| `portworld ops export-memory` | unchanged under `ops` | keep |
| `portworld ops migrate-storage-layout` | unchanged under `ops` | keep |

New commands to add without breaking the current surface:

- `portworld status`
- `portworld logs`
- `portworld config`
- `portworld providers`
- `portworld update`

## Example User Flows

### 1. First-time local setup

```bash
portworld init
portworld doctor --target local
docker compose up --build
```

### 2. First managed deploy

```bash
portworld init
portworld doctor --target gcp-cloud-run --project <project> --region <region>
portworld deploy gcp-cloud-run --project <project> --region <region>
```

### 3. Repeat managed deploy

```bash
portworld status
portworld deploy gcp-cloud-run
```

### 4. Config update

```bash
portworld config edit providers
portworld config edit security
```

### 5. Advanced maintenance

```bash
portworld ops check-config --full-readiness
portworld ops export-memory --output /tmp/portworld-memory-export.zip
```

## Open Follow-Up Items For The Implementation Plan

The later implementation plan should answer:

- when `status`, `logs`, `config`, `providers`, and `update` land
- when `.portworld/project.json` becomes the primary product config surface
- when the public CLI moves out of `backend/`
- what deprecation policy, if any, will later apply to compatibility-era command shapes

## Acceptance Criteria

This command-contract doc is complete when:

- another engineer can implement the next public CLI surfaces without inventing command names or hierarchy
- compatibility behavior for the current CLI is explicit
- interactive and non-interactive behavior is explicit
- provider-target naming is constrained enough to prevent command-shape sprawl
- the later implementation plan can build directly on this doc without re-deciding product shape

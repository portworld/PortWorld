# PortWorld CLI Product Direction

## Summary

This document is forward-looking.
For the current v1 implementation record and current contract, see:

- [BACKEND_CLI_IMPLEMENTATION_PLAN.md](./BACKEND_CLI_IMPLEMENTATION_PLAN.md)
- [BACKEND_CLI_SPEC.md](./BACKEND_CLI_SPEC.md)

This document defines the next high-level direction for the public `portworld` CLI after the current backend CLI/runtime implementation plan.

The current CLI successfully covers:

- local setup
- local and GCP readiness checks
- managed GCP Cloud Run deploys
- backend operator tasks under `portworld ops`

The next step is to treat `portworld` as the public setup, deploy, and lifecycle tool for the open-source PortWorld framework rather than only as backend-adjacent operator tooling.

That means the CLI should become the primary way a new user:

- installs PortWorld
- chooses model and tool providers
- configures local and managed runtime settings
- deploys to supported cloud providers
- inspects and updates existing deployments over time

## Product Goal

The product goal is a setup and deployment experience that feels close to:

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
portworld init
portworld deploy gcp-cloud-run
```

while keeping the real logic in the versioned Python CLI rather than in shell scripts.

## Product Principles

### 1. Installer is thin

The install script should only:

- verify the minimum local prerequisites
- install the `portworld` CLI
- hand off to `portworld init`

The install script should not own:

- provider logic
- deploy logic
- secret handling logic
- config schema logic

Those belong in the CLI itself.

### 2. CLI is the real product surface

`portworld` should be the primary public interface for:

- first-run setup
- validation
- deployment
- updates
- operational inspection

### 3. Local prerequisites stay minimal

The public install path should assume as little as possible beyond:

- `bash`
- `curl`
- `python3`

Tooling such as `gcloud` and Docker should be validated only when the user chooses workflows that require them.

`uv` should remain an internal/developer tool unless there is a strong public-product reason to expose it.

### 4. Cloud builds should happen in cloud services

For managed deploy targets, local machines should mostly be responsible for:

- configuration
- auth
- deploy orchestration

The actual image build and managed resource provisioning should happen through the cloud provider path whenever possible.

### 5. Secrets do not live in project metadata

Project metadata under `.portworld/` should be non-secret.

Managed deploy secrets should be pushed into the provider secret manager.
Local runtime compatibility may still require generating `backend/.env`, but `.portworld/` should not become a secret store.

### 6. Wizard first, operator second

New users should start with guided setup.
Advanced users should still have lower-level commands, but those should remain secondary.

## Intended Public Command Surface

The CLI should evolve toward this top-level command model:

- `portworld init`
- `portworld doctor`
- `portworld deploy`
- `portworld status`
- `portworld logs`
- `portworld config`
- `portworld providers`
- `portworld update`
- `portworld ops`

### Role of each command

`portworld init`

- first-run wizard
- resumable setup
- generate local runtime config
- optionally offer immediate deploy

`portworld doctor`

- validate local setup
- validate provider auth and requirements
- validate managed deploy readiness
- explain exactly what is missing

`portworld deploy`

- managed deployment entrypoint
- provider-specific subcommands
- cloud-first build and provision workflow

`portworld status`

- show current project mode
- show latest deploy target
- show service URL and deployment summary
- show health/readiness summary

`portworld logs`

- provider-aware logs access
- Cloud Run first

`portworld config`

- inspect config
- edit only one config section
- rotate or refresh settings
- rerun specific wizard sections

`portworld providers`

- list supported cloud providers
- list supported model/tool providers
- show requirements and capability notes

`portworld update`

- update the CLI
- update the deployed backend version or image

`portworld ops`

- advanced and lower-level operator functions
- wraps the current backend maintenance commands

## Wizard Direction

The setup wizard should be staged, not a single monolithic prompt sequence.

Recommended sections:

1. project mode
   - local only
   - managed cloud
2. cloud provider
   - GCP first
3. model providers
   - realtime provider
   - vision provider
   - search/tool provider
4. tools
   - choose allowed tools and feature toggles
5. secrets
   - API keys
   - bearer token strategy
6. security
   - CORS origins
   - allowed hosts
7. deploy defaults
   - region
   - service name
   - scaling defaults
8. review and apply
   - config summary
   - readiness check
   - deploy now prompt

The wizard should later support partial reruns through commands such as:

- `portworld config edit providers`
- `portworld config edit security`
- `portworld config edit cloud`

## Config And State Direction

The CLI needs a clearer separation between public project config, generated runtime config, and deploy metadata.

### 1. Project config

Recommended path:

- `.portworld/project.json`

This should hold non-secret project choices such as:

- cloud provider
- enabled features
- selected providers
- allowed tools
- preferred region
- service naming defaults
- non-secret security posture defaults

### 2. Generated runtime config

Recommended generated artifact:

- `backend/.env`

This remains useful for:

- local runtime compatibility
- Docker Compose
- backend Python startup

But it should increasingly be treated as a generated runtime artifact rather than the highest-level product config surface.

### 3. Deploy metadata

Recommended path:

- `.portworld/state/*.json`

This should store non-secret deploy state such as:

- last deploy target
- service URL
- resource names
- last deploy timestamps

## Provider Model Direction

The CLI should formalize two kinds of providers.

### Cloud providers

Examples:

- GCP Cloud Run
- future AWS / Railway / Fly.io / others

Each cloud provider definition should declare:

- install requirements
- auth requirements
- supported deploy targets
- secret-management model
- log/status capabilities
- managed storage support expectations

### Model and tool providers

Examples:

- OpenAI
- Mistral
- Tavily

Each provider definition should declare:

- required env keys
- optional env keys
- supported features
- validation rules
- human-facing setup notes

This should eventually allow `portworld providers` to act as a real discovery surface instead of a static doc pointer.

## Installer Direction

The intended public distribution paths should be:

### 1. `pipx`

Preferred long-term installation model:

```bash
pipx install portworld
```

### 2. Thin shell bootstrap

Convenience path:

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

That script should:

- detect OS and architecture
- verify `python3`
- install `pipx` or another isolated CLI mechanism if required
- install or upgrade `portworld`
- run or suggest `portworld init`

The script should also validate provider-specific CLIs only when required by user flow.

For example, if the user chooses GCP, the CLI should validate:

- `gcloud` installed
- `gcloud` authenticated
- `gcloud` project selected

If missing, the CLI should explain the fix clearly and guide the login flow rather than burying the user in cloud-console-only instructions.

## Repo Structure Direction

The current CLI lives under:

- `backend/cli_app/`

This was a pragmatic choice for the current implementation phase, but it is likely the wrong long-term home if `portworld` becomes the public framework entrypoint.

### Short term

Keep iterating in the current layout while:

- stabilizing the UX
- defining the public config/state model
- expanding lifecycle commands beyond deploy

### Medium term

Extract the public CLI into a top-level package, for example:

- `portworld_cli/`

while keeping `backend/` focused on the deployable runtime and backend-specific adapters.

The expected long-term split is:

- public CLI package
- backend runtime package
- iOS app
- shared docs and examples

## Proposed Next Planning Documents

This product-direction doc should be followed by more concrete design docs:

1. command tree and UX contract
2. `.portworld` project config and state schema
3. installer specification
4. CLI extraction and repo-structure migration plan

## Open Product Questions

These questions should be resolved before large v2 implementation work starts:

- Should `backend/.env` remain the user-visible primary config file, or become mostly generated?
- Should the public install path prefer `pipx` only, or officially support both `pipx` and the shell bootstrap equally?
- Which provider set is in-scope for the first public release beyond GCP?
- How much of the backend operator surface should remain exposed directly under `portworld ops`?
- At what point should the CLI move out of `backend/` into its own package boundary?

# PortWorld CLI

`portworld` is the public setup, validation, deploy, and lifecycle CLI for PortWorld.

It supports two primary workflows:

- operator path: a zero-clone published workspace backed by the released backend image
- contributor path: a source checkout workflow for local development and repo-backed changes

## Install

Public install path:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
```

The bootstrap installs `uv` automatically, downloads Python 3.11+ when needed, and bootstraps
Node.js/npm/npx in user space when needed for Node-based MCP stdio launchers. Published/container
workspaces use the Node runtime baked into the backend image instead of depending on the host PATH.

Manual fallback for a pinned release:

```bash
uv tool install "portworld==<version>"
portworld init
```

TestPyPI beta validation:

```bash
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "portworld==<version>"
```

```bash
uv tool install --default-index https://test.pypi.org/simple --index https://pypi.org/simple "portworld==<version>"
```

The bare TestPyPI page snippet may fail because TestPyPI does not necessarily host every transitive dependency.

## Operator Quickstart

The default public path is the operator-friendly published workspace flow:

```bash
portworld init
cd ~/.portworld/stacks/default
docker compose up -d
portworld doctor --target local
portworld status
```

This flow:

- creates a local published workspace
- pins a released backend image
- lets you run PortWorld locally without cloning the repo
- uses the backend image as the runtime source of truth for Node MCP stdio prerequisites

Example extension manifests for the filesystem MCP server:

- local/source runtime: [mcp-filesystem-local.extensions.json](/Users/pierrehaas/.codex/worktrees/30fa/PortWorld/docs/operations/examples/mcp-filesystem-local.extensions.json)
- published/container runtime: [mcp-filesystem-published.extensions.json](/Users/pierrehaas/.codex/worktrees/30fa/PortWorld/docs/operations/examples/mcp-filesystem-published.extensions.json)

## Contributor Path

For a source checkout workflow, run from the repo root:

```bash
pipx install . --force
portworld init
```

Use this path when you are developing PortWorld itself or need repo-backed runtime artifacts.

## Managed Deploy

Managed targets (MVP): `gcp-cloud-run`, `aws-ecs-fargate`, `azure-container-apps`.

Managed deploy examples:

```bash
portworld doctor --target gcp-cloud-run --project <project> --region <region>
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com

portworld doctor --target aws-ecs-fargate --aws-region <region>
portworld deploy aws-ecs-fargate --region <region> --cors-origins https://app.example.com

portworld doctor --target azure-container-apps --azure-subscription <subscription> --azure-resource-group <resource-group> --azure-region <region>
portworld deploy azure-container-apps --subscription <subscription> --resource-group <resource-group> --region <region> --cors-origins https://app.example.com
```

Published workspaces can drive any managed target after initial target configuration.

Managed storage shape for these targets:

- object storage is the source of truth for memory files
- Postgres remains for operational metadata (session/frame indexes) in the current MVP backend
- `gcp-cloud-run`: Cloud Run + GCS + Cloud SQL Postgres
- `aws-ecs-fargate`: ECS/Fargate + CloudFront + ALB + S3 + Postgres operational metadata
- `azure-container-apps`: Container Apps + Blob Storage + Postgres operational metadata

## Main Commands

- `portworld init` initializes or refreshes a source checkout or published workspace
- `portworld doctor` validates local or managed readiness
- `portworld deploy` deploys PortWorld to a managed target
- `portworld status` shows current workspace and deploy state
- `portworld logs` reads managed deployment logs for GCP, AWS, and Azure
- `portworld config` inspects or edits project configuration
- `portworld providers` lists supported provider integrations
- `portworld update` updates the CLI or redeploys the active managed target across GCP, AWS, and Azure
- `portworld ops` runs lower-level backend operator tasks

Managed target log commands:

```bash
portworld logs gcp-cloud-run --since 24h --limit 50
portworld logs aws-ecs-fargate --since 24h --limit 50
portworld logs azure-container-apps --since 24h --limit 50
```

`portworld update deploy` redeploys whichever managed target is currently active in workspace state/config.

Current MVP hardening note:

- AWS one-click currently provisions RDS with public accessibility and broad ingress
- Azure one-click currently provisions PostgreSQL with public access
- keep these defaults for MVP validation only; tighten them before production use

## Updates

For CLI updates:

```bash
uv tool upgrade portworld
```

You can also rerun the installer or pin a specific released version:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash -s -- --version <tag>
```

## More Docs

- backend runtime and self-hosting details: `backend/README.md`
- self-host quickstart and operator notes: `docs/operations/BACKEND_SELF_HOSTING.md`
- release process and TestPyPI/PyPI notes: `docs/operations/CLI_RELEASE_PROCESS.md`
- changelog and release history: `CHANGELOG.md`

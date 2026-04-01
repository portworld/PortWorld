# PortWorld CLI

`portworld` is the command-line interface for bootstrapping PortWorld, validating local or cloud environments, and deploying PortWorld to supported managed targets.

It supports two primary workflows:

- published workspace: run PortWorld locally from a released backend image without cloning the repo
- source checkout: work from a PortWorld repository clone for development and repo-backed changes

## Who This Is For

Use `portworld` if you want to:

- start a local PortWorld workspace quickly
- validate local or managed deployment readiness
- deploy PortWorld to GCP Cloud Run, AWS ECS/Fargate, or Azure Container Apps
- inspect current workspace state, providers, extensions, and managed logs

## Requirements

- macOS or Linux
- Python 3.11+
- Docker for local published-workspace runs

## Install

Recommended:

```bash
uv tool install portworld
```

Alternative with `pipx`:

```bash
pipx install portworld
```

Bootstrap installer:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
```

The bootstrap installer can install `uv`, provision Python 3.11+ when needed, and bootstrap Node.js tooling for MCP launchers.

## Quickstart

The default public flow is a published workspace backed by a released backend image:

```bash
portworld init
cd ~/.portworld/stacks/default
docker compose up -d
portworld doctor --target local
portworld status
```

`portworld init` supports two setup modes:

- `quickstart`: minimal prompts with safe defaults
- `manual`: fuller explicit setup flow

You can force either mode:

```bash
portworld init --setup-mode quickstart
portworld init --setup-mode manual
```

Example extension manifests for the filesystem MCP server:

- local/source runtime: [docs/operations/examples/mcp-filesystem-local.extensions.json](https://github.com/portworld/PortWorld/blob/main/docs/operations/examples/mcp-filesystem-local.extensions.json)
- published/container runtime: [docs/operations/examples/mcp-filesystem-published.extensions.json](https://github.com/portworld/PortWorld/blob/main/docs/operations/examples/mcp-filesystem-published.extensions.json)

## Source Checkout Workflow

Use a repo checkout when you are developing PortWorld itself:

```bash
git clone https://github.com/portworld/PortWorld.git
cd PortWorld
pipx install . --force
portworld init
```

Source-checkout installs are intended for contributors, local development, and repo-backed debugging.

## Managed Deploys

Supported managed targets:

- `gcp-cloud-run`
- `aws-ecs-fargate`
- `azure-container-apps`

Typical readiness flow:

```bash
portworld doctor --target gcp-cloud-run --gcp-project <project> --gcp-region <region>
portworld doctor --target aws-ecs-fargate --aws-region <region>
portworld doctor --target azure-container-apps --azure-subscription <subscription> --azure-resource-group <resource-group> --azure-region <region>
```

Typical deploy flow:

```bash
portworld deploy gcp-cloud-run --project <project> --region <region>
portworld deploy aws-ecs-fargate --region <region>
portworld deploy azure-container-apps --subscription <subscription> --resource-group <resource-group> --region <region>
```

Managed log examples:

```bash
portworld logs gcp-cloud-run --since 24h --limit 50
portworld logs aws-ecs-fargate --since 24h --limit 50
portworld logs azure-container-apps --since 24h --limit 50
```

To redeploy the active managed target from current workspace state:

```bash
portworld update deploy
```

## Main Commands

- `portworld init`: initialize or refresh a published workspace or source checkout
- `portworld doctor`: validate local or managed readiness
- `portworld deploy`: deploy PortWorld to a managed target
- `portworld status`: inspect workspace and deploy state
- `portworld logs`: read managed deployment logs
- `portworld config`: inspect or edit project configuration
- `portworld providers`: inspect supported realtime, vision, search, and cloud providers
- `portworld extensions`: manage official or local extension manifests and install state
- `portworld update cli`: show the recommended CLI upgrade command for the current install mode
- `portworld update deploy`: redeploy the active managed target
- `portworld ops`: run lower-level operator tasks

Common low-level operator tasks:

```bash
portworld ops check-config
portworld ops check-config --full-readiness
portworld ops bootstrap-storage
portworld ops export-memory --output /tmp/portworld-memory-export.zip
```

## Updating

Upgrade an installed CLI:

```bash
uv tool upgrade portworld
```

Install a pinned release:

```bash
uv tool install "portworld==<version>"
```

Run the bootstrap installer for a specific tag:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash -s -- --version v<version>
```

## TestPyPI

For TestPyPI validation:

```bash
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "portworld==<version>"
```

```bash
uv tool install --default-index https://test.pypi.org/simple --index https://pypi.org/simple "portworld==<version>"
```

The bare install snippet shown on TestPyPI may be incomplete if not every transitive dependency is hosted there.

## Production Caution

The managed cloud workflows are usable, but some infrastructure defaults are still MVP-oriented:

- AWS one-click deploy currently provisions RDS with public accessibility and broad ingress
- Azure one-click deploy currently provisions PostgreSQL with public access

Treat those defaults as validation-oriented until production hardening is complete.

## More Documentation

- Backend runtime and self-hosting: [backend/README.md](https://github.com/portworld/PortWorld/blob/main/backend/README.md)
- CLI release process: [docs/operations/CLI_RELEASE_PROCESS.md](https://github.com/portworld/PortWorld/blob/main/docs/operations/CLI_RELEASE_PROCESS.md)
- Changelog: [CHANGELOG.md](https://github.com/portworld/PortWorld/blob/main/CHANGELOG.md)

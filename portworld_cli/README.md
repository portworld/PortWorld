# PortWorld CLI

Command-line interface for bootstrapping, validating, and deploying [PortWorld](https://github.com/portworld/PortWorld) — the open-source runtime for voice-and-vision AI assistants.

## Install

**Recommended** (with [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install portworld
```

With pipx:

```bash
pipx install portworld
```

Bootstrap installer (installs `uv`, Python 3.11+, and Node.js tooling if missing):

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash
```

## Agent skill (Cursor, Codex, others)

Install the **portworld-cli-autopilot** skill so agents get consistent bootstrap and operator commands ([Agent Skills](https://agentskills.io) format):

```bash
npx skills add portworld/PortWorld --skill portworld-cli-autopilot -a cursor -a codex -y
```

Direct install of the skill folder only:

```bash
npx skills add https://github.com/portworld/PortWorld/tree/main/skills/portworld-cli-autopilot -y
```

See [skills/README.md](https://github.com/portworld/PortWorld/blob/main/skills/README.md) for `--list`, `-g` (global), and telemetry options.

## Requirements

- macOS or Linux
- Python 3.11+
- Docker for local published-workspace runs

## Quickstart

Initialize a local workspace, configure providers, and start the backend:

```bash
portworld init
```

Validate and inspect:

```bash
portworld doctor --target local
portworld status
```

`portworld init` supports two setup modes:

- `quickstart` — guided onboarding with beginner-friendly defaults
- `manual` — guided onboarding with advanced choices like local source runtime

Force either mode with `--setup-mode quickstart` or `--setup-mode manual`.

## Commands

| Command | Description |
|---------|-------------|
| `portworld init` | Run the onboarding wizard, write config, and execute the selected local or managed path |
| `portworld doctor` | Validate local or managed deployment readiness |
| `portworld deploy` | Deploy to a managed cloud target |
| `portworld status` | Inspect workspace and deploy state |
| `portworld logs` | Read managed deployment logs |
| `portworld config` | Inspect or edit project configuration |
| `portworld providers` | Browse supported realtime, vision, search, and cloud providers |
| `portworld extensions` | Manage extension manifests and install state |
| `portworld update cli` | Show the recommended CLI upgrade command |
| `portworld update deploy` | Redeploy the active managed target |
| `portworld ops` | Lower-level operator tasks (see below) |

### Operator Tasks

```bash
portworld ops check-config                   # validate local config
portworld ops check-config --full-readiness  # full preflight with provider validation
portworld ops bootstrap-storage              # initialize storage
portworld ops export-memory --output /tmp/portworld-memory-export.zip
```

## Deploy Workflows

Supported managed targets: **GCP Cloud Run**, **AWS ECS/Fargate**, **Azure Container Apps**.

### Readiness Check

```bash
portworld doctor --target gcp-cloud-run    --gcp-project <project> --gcp-region <region>
portworld doctor --target aws-ecs-fargate  --aws-region <region>
portworld doctor --target azure-container-apps --azure-subscription <sub> --azure-resource-group <rg> --azure-region <region>
```

### Deploy

```bash
portworld deploy gcp-cloud-run   --project <project> --region <region>
portworld deploy aws-ecs-fargate --region <region>
portworld deploy azure-container-apps --subscription <sub> --resource-group <rg> --region <region>
```

### Logs

```bash
portworld logs gcp-cloud-run       --since 24h --limit 50
portworld logs aws-ecs-fargate     --since 24h --limit 50
portworld logs azure-container-apps --since 24h --limit 50
```

### Redeploy

```bash
portworld update deploy
portworld update deploy --tag <image-tag>
```

## Source Checkout

Use a repo checkout when developing PortWorld itself:

```bash
git clone https://github.com/portworld/PortWorld.git
cd PortWorld
pipx install . --force
portworld init
```

## Updating

Upgrade the CLI:

```bash
uv tool upgrade portworld
```

Install a pinned version:

```bash
uv tool install "portworld==<version>"
```

Upgrade via the bootstrap installer:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/portworld/PortWorld/main/install.sh | bash -s -- --version v<version>
```

## Production Caution

Managed cloud workflows are supported, but some infrastructure defaults favor quick bring-up over locked-down security:

- **AWS**: one-click deploy provisions RDS with public accessibility and broad ingress
- **Azure**: one-click deploy provisions PostgreSQL with public access

Review and harden these defaults before exposing a deployment to the public internet.

## Links

- [Repository](https://github.com/portworld/PortWorld)
- [Backend README](https://github.com/portworld/PortWorld/blob/main/backend/README.md) — runtime, API reference, configuration
- [iOS README](https://github.com/portworld/PortWorld/blob/main/IOS/README.md) — iOS app setup, Meta DAT, permissions
- [Getting Started](https://github.com/portworld/PortWorld/blob/main/GETTING_STARTED.md) — extended onboarding guide
- [Changelog](https://github.com/portworld/PortWorld/blob/main/CHANGELOG.md)
- [CLI Release Process](https://github.com/portworld/PortWorld/blob/main/docs/operations/CLI_RELEASE_PROCESS.md)

## License

MIT — see [LICENSE](https://github.com/portworld/PortWorld/blob/main/LICENSE).

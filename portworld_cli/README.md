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

The bootstrap installs `uv` automatically and downloads Python 3.11+ when needed.

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

## Contributor Path

For a source checkout workflow, run from the repo root:

```bash
pipx install . --force
portworld init
```

Use this path when you are developing PortWorld itself or need repo-backed runtime artifacts.

## Managed Deploy

For Cloud Run deploys:

```bash
portworld doctor --target gcp-cloud-run --project <project> --region <region>
portworld deploy gcp-cloud-run --project <project> --region <region> --cors-origins https://app.example.com
```

Published workspaces can also drive managed deploys once configured.

## Main Commands

- `portworld init` initializes or refreshes a source checkout or published workspace
- `portworld doctor` validates local or managed readiness
- `portworld deploy` deploys PortWorld to a managed target
- `portworld status` shows current workspace and deploy state
- `portworld logs` reads managed deployment logs
- `portworld config` inspects or edits project configuration
- `portworld providers` lists supported provider integrations
- `portworld update` updates the CLI or redeploys the active managed target
- `portworld ops` runs lower-level backend operator tasks

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
- self-host quickstart and operator notes: `docs/BACKEND_SELF_HOSTING.md`
- release process and TestPyPI/PyPI notes: `docs/CLI_RELEASE_PROCESS.md`

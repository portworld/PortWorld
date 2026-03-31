---
name: portworld-cli-autopilot
description: Install, initialize, validate, and operate the PortWorld CLI end-to-end without asking developers to run setup manually. Use when working in the PortWorld repo and needing to bootstrap or re-bootstrap CLI config, run local readiness checks, inspect status/logs, or execute managed deploy flows (GCP/AWS/Azure) through `portworld` commands.
---

# PortWorld CLI Autopilot

## Overview
Automate PortWorld CLI setup and operation with non-interactive defaults so the agent performs setup work directly instead of delegating setup steps to developers.

## Use This Flow
1. Run bootstrap first using `scripts/bootstrap_portworld_cli.sh`.
2. Run the requested PortWorld CLI task (`doctor`, `status`, `logs`, `deploy`, `config`, `providers`, `update`).
3. Verify with `doctor`/`status` before and after material changes.

For detailed command matrices and target-specific examples, read [references/command-map.md](references/command-map.md).

## Bootstrap Workflow
1. Confirm `uv` exists. If missing, stop and ask for permission to install `uv`.
2. Run bootstrap script from repo root:
```bash
./skills/portworld-cli-autopilot/scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode source
```
3. For published workspace bootstrap:
```bash
./skills/portworld-cli-autopilot/scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode published --stack-name default
```
4. Treat bootstrap as idempotent. Re-run when config drift or missing runtime prerequisites are detected.

## Operating Rules
1. Prefer non-interactive CLI execution in automation contexts.
2. Prefer explicit flags over prompts.
3. Keep defaults unless the user asked for custom provider/deploy shape.
4. Use source mode for repo development tasks.
5. Use published mode for operator-style local runtime when source checkout behavior is not needed.

## Provider and Secrets Policy
1. Use `OPENAI_API_KEY` when present.
2. Fallback to `GEMINI_LIVE_API_KEY` when OpenAI key is absent.
3. If neither key exists, stop with one concise request for a key; do not continue with a half-configured setup.
4. Keep vision/tooling disabled by default during bootstrap unless the user explicitly requests them.

## Verification Gates
1. After bootstrap: run `doctor --target local` and `status`.
2. Before managed deploy: run target-specific `doctor`.
3. After managed deploy: run target-specific `doctor` and collect `status`.

## Common Task Shortcuts
1. Validate local runtime:
```bash
uv run python -m portworld_cli.main doctor --target local
```
2. Show workspace/deploy state:
```bash
uv run python -m portworld_cli.main status
```
3. Show config:
```bash
uv run python -m portworld_cli.main config show
```
4. List providers:
```bash
uv run python -m portworld_cli.main providers list
```

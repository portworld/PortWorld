---
name: portworld-cli-autopilot
description: Installs, initializes, validates, and operates the PortWorld `portworld` CLI end-to-end with non-interactive defaults. Use when the user needs CLI bootstrap or re-bootstrap, local readiness checks (`doctor`, `status`), managed deploy flows (GCP, AWS, Azure), logs, or config/providers work. Applies to a PortWorld git checkout, a published CLI install on PATH, or agents using this skill after `npx skills add`.
---

# PortWorld CLI Autopilot

## Overview

Automate PortWorld CLI setup and operation so the agent runs concrete commands instead of asking the user to perform setup steps manually.

## Use This Flow

1. Run bootstrap first using `scripts/bootstrap_portworld_cli.sh` (see [Bootstrap](#bootstrap-workflow)).
2. Run the requested PortWorld CLI task (`doctor`, `status`, `logs`, `deploy`, `config`, `providers`, `update`).
3. Verify with `doctor` / `status` before and after material changes.

For detailed command matrices and target-specific examples, read [references/command-map.md](references/command-map.md).

## CLI entry (choose one)

- **`portworld`** — use when the CLI is installed from PyPI/pipx/uv tool and on `PATH` (typical for operators and many agents).
- **`uv run python -m portworld_cli.main`** — use in a **repo checkout** with `uv sync` / editable install, or when debugging against source.

Do not assume `uv` is available unless the workspace is a PortWorld clone with a synced environment.

## Bootstrap Workflow

Bootstrap needs **`uv`**, a PortWorld **project root** (repo checkout or published workspace root), and **`OPENAI_API_KEY` or `GEMINI_LIVE_API_KEY`** in the environment.

1. Confirm `uv` exists. If missing, stop and ask for permission to install `uv`.
2. **Change to the skill directory** (the folder that contains this `SKILL.md` — after `npx skills add`, that is the installed skill root, e.g. `.agents/skills/portworld-cli-autopilot` or `skills/portworld-cli-autopilot` in a clone).
3. Run:

```bash
bash scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode source
```

4. For published workspace bootstrap:

```bash
bash scripts/bootstrap_portworld_cli.sh --project-root "<repo-root>" --mode published --stack-name default
```

5. Treat bootstrap as idempotent. Re-run when config drift or missing runtime prerequisites are detected.

## Operating Rules

1. Prefer non-interactive CLI execution in automation contexts.
2. Prefer explicit flags over prompts.
3. Keep defaults unless the user asked for custom provider or deploy shape.
4. Use **source** mode for repo development tasks.
5. Use **published** mode for operator-style local runtime when a source checkout is not required.

## Provider and Secrets Policy

1. Use `OPENAI_API_KEY` when present.
2. Fallback to `GEMINI_LIVE_API_KEY` when the OpenAI key is absent.
3. If neither key exists, stop with one concise request for a key; do not continue with a half-configured setup.
4. Keep vision and tooling disabled by default during bootstrap unless the user explicitly requests them.

## Verification Gates

1. After bootstrap: run `doctor --target local` and `status`.
2. Before managed deploy: run target-specific `doctor`.
3. After managed deploy: run target-specific `doctor` and collect `status`.

## Common Task Shortcuts

Prefer `portworld` when available; otherwise use `uv run` as in [references/command-map.md](references/command-map.md).

1. Validate local runtime:

```bash
portworld doctor --target local
```

2. Show workspace/deploy state:

```bash
portworld status
```

3. Show config:

```bash
portworld config show
```

4. List providers:

```bash
portworld providers list
```

# Agent skills (PortWorld)

This directory holds [Agent Skills](https://agentskills.io)-style packages: each subfolder contains a `SKILL.md` plus optional `scripts/` and `references/`.

They are installable with the [Vercel Labs `skills` CLI](https://github.com/vercel-labs/skills) (`npx skills`), which copies or symlinks skills into each coding agent’s expected paths (for example Cursor under `.agents/skills/` / `~/.cursor/skills/`).

## Install from this monorepo

Use the GitHub `owner/repo` shorthand or a **tree URL** that points at a single skill folder.

**List skills without installing** (verify discovery):

```bash
npx skills add https://github.com/portworld/PortWorld/tree/main/skills/portworld-cli-autopilot --list
```

**Install the PortWorld CLI skill** (non-interactive; Cursor + Codex):

```bash
npx skills add portworld/PortWorld --skill portworld-cli-autopilot -a cursor -a codex -y
```

**Install via direct tree URL** (stable for docs when you want one skill only):

```bash
npx skills add https://github.com/portworld/PortWorld/tree/main/skills/portworld-cli-autopilot -a cursor -y
```

**User-wide install** (all projects):

```bash
npx skills add portworld/PortWorld --skill portworld-cli-autopilot -a cursor -a codex -g -y
```

Optional: set `DISABLE_TELEMETRY=1` if you want to opt out of the CLI’s anonymous telemetry ([upstream docs](https://github.com/vercel-labs/skills)).

## Optional: dedicated skills-only repository

For a smaller clone URL (only skills, no app/backend tree), you can publish a separate public repo (for example `portworld/agent-skills`) whose root or `skills/` layout matches the same `SKILL.md` contract, then document:

```bash
npx skills add portworld/agent-skills --skill portworld-cli-autopilot -y
```

That is optional; the canonical source for PortWorld remains this monorepo unless you add automation to sync a second repo.

## Skills in this repo

| Directory | Purpose |
|-----------|---------|
| [`portworld-cli-autopilot/`](portworld-cli-autopilot/) | Bootstrap and operate the `portworld` CLI (doctor, deploy, logs, config) |

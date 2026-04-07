---
name: openclaw-gateway-bridge
description: Configure and harden remote OpenClaw gateway connectivity for PortWorld across cloud providers. Use when OpenClaw runs on a VM/VPS/ECS host different from PortWorld, and the user wants the agent to perform end-to-end setup (exposure method, auth, reverse proxy/tunnel, PortWorld env wiring, and validation) with minimal manual steps.
---

# OpenClaw Gateway Bridge

## Overview

Use this skill when OpenClaw and PortWorld run on different hosts/clouds and the user wants a working, secure integration without provider-specific assumptions.

Default production posture:

1. Keep OpenClaw gateway auth enabled (`token` mode by default).
2. Expose a stable HTTPS endpoint (reverse proxy or private mesh ingress).
3. Point PortWorld at that endpoint using `OPENCLAW_BASE_URL` and `OPENCLAW_AUTH_TOKEN`.

Do not expose unauthenticated raw gateway ports to the public internet.

## Use This Flow

1. Discover OpenClaw gateway status, auth mode, and API port on the OpenClaw host.
2. Select connectivity mode:
   - `prod-https` (recommended): stable HTTPS endpoint to OpenClaw API.
   - `private-mesh`: private overlay network endpoint (tailnet/VPN).
   - `dev-tunnel`: SSH local forward (development only).
3. Apply host changes (bind/proxy/firewall/auth) for selected mode.
4. Wire PortWorld env (`OPENCLAW_*`) and run `portworld doctor`.
5. Prove end-to-end with `/v1/models` and delegated task lifecycle.

For detailed command recipes, use [references/runbook.md](references/runbook.md).

## Discovery Commands

On the OpenClaw host, run the bundled script first:

```bash
bash scripts/discover_openclaw_gateway.sh
```

If running remotely:

```bash
ssh <user>@<host> 'bash -s' < scripts/discover_openclaw_gateway.sh
```

This script reports:

- gateway service state
- auth mode
- listeners and candidate API ports
- `/v1/models` probe results per port

## Connectivity Decision Rules

Pick exactly one mode and explain why:

1. `prod-https` when PortWorld is managed/prod (Cloud Run, ECS, etc.).
2. `private-mesh` when both sides already run on private mesh and no public ingress is needed.
3. `dev-tunnel` only for local development or temporary operator sessions.

If user asks for provider-agnostic setup and does not force an option, choose `prod-https`.

## Required PortWorld Wiring

Always set these on the PortWorld runtime:

```bash
OPENCLAW_ENABLED=true
REALTIME_TOOLING_ENABLED=true
OPENCLAW_BASE_URL=<scheme://host[:port]>
OPENCLAW_AUTH_TOKEN=<gateway token>
OPENCLAW_AGENT_ID=openclaw/default
```

Notes:

- `OPENCLAW_BASE_URL` is the root URL only (no `/v1/...` suffix).
- PortWorld uses OpenClaw HTTP endpoints (`/v1/models`, `/v1/responses`). No WebSocket setup is required for PortWorld delegation.

## Validation Gates

Pass all gates before declaring setup complete:

1. OpenClaw-side probe:
   - `curl -i "$BASE_URL/v1/models" -H "Authorization: Bearer $TOKEN"` returns `200`.
2. PortWorld-side doctor:
   - `portworld doctor --target local` (or target-specific doctor if applicable) shows OpenClaw checks passing.
3. Delegation flow:
   - `delegate_to_openclaw` returns `task_id`.
   - `openclaw_task_status` reaches terminal state (`succeeded|failed|cancelled`).

## Security Guardrails

1. Never print or log full tokens in outputs.
2. Prefer private/allowlisted ingress over internet-wide exposure.
3. If using `trusted-proxy` auth mode, ensure direct gateway access is blocked and only proxy-origin traffic is accepted.
4. If non-loopback bind is enabled, verify firewall rules are tightened before finalizing.


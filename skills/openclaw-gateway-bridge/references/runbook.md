# OpenClaw Gateway Bridge Runbook

This runbook provides provider-agnostic command patterns for connecting PortWorld to OpenClaw when they run on separate hosts/clouds.

## 1) OpenClaw host discovery

Run:

```bash
bash scripts/discover_openclaw_gateway.sh
```

Manual fallback:

```bash
systemctl --user status openclaw-gateway --no-pager
openclaw config get gateway.auth.mode
sudo ss -ltnp | rg -i 'openclaw|LISTEN'
```

Port verification:

```bash
TOKEN="$(openclaw config get gateway.auth.token | tail -n 1)"
for p in 18789 18791 18792; do
  echo "=== $p ==="
  curl -sS -o /tmp/oc.$p.out -w "HTTP %{http_code}\n" \
    -H "Authorization: Bearer $TOKEN" \
    "http://127.0.0.1:$p/v1/models"
  head -c 200 /tmp/oc.$p.out; echo; echo
done
```

Use the port that returns JSON + `HTTP 200` for `/v1/models`.

## 2) Recommended production mode: stable HTTPS endpoint

Use when PortWorld runs in managed environments (Cloud Run, ECS, etc.).

Pattern:

1. Keep OpenClaw API internal on host (`127.0.0.1:<API_PORT>` or private bind).
2. Terminate TLS at reverse proxy (Caddy/Nginx/Traefik/Envoy).
3. Proxy to OpenClaw API port.
4. Restrict ingress by firewall or proxy policy.

### Caddy example (Linux host)

```bash
sudo apt-get update
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
openclaw.example.com {
  reverse_proxy 127.0.0.1:18791
}
EOF
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Verify:

```bash
curl -i "https://openclaw.example.com/v1/models" \
  -H "Authorization: Bearer $TOKEN"
```

## 3) Private mesh mode

Use when both runtimes are on a private network/overlay and public ingress is unnecessary.

Set PortWorld:

```bash
OPENCLAW_BASE_URL=http://<private-host-or-tailnet-name>:<api-port>
OPENCLAW_AUTH_TOKEN=<token>
```

Still keep gateway auth enabled.

## 4) Development mode: SSH local forward

Use for local testing only.

From PortWorld host:

```bash
ssh -fN -o ExitOnForwardFailure=yes \
  -L 18791:127.0.0.1:18791 \
  <user>@<openclaw-host>
```

Set PortWorld:

```bash
OPENCLAW_BASE_URL=http://127.0.0.1:18791
OPENCLAW_AUTH_TOKEN=<token>
```

## 5) PortWorld wiring

Use CLI init flags when available:

```bash
portworld init --with-openclaw \
  --openclaw-url "<OPENCLAW_BASE_URL>" \
  --openclaw-token "<OPENCLAW_AUTH_TOKEN>" \
  --openclaw-agent-id "openclaw/default"
```

Or write env keys directly:

```bash
OPENCLAW_ENABLED=true
REALTIME_TOOLING_ENABLED=true
OPENCLAW_BASE_URL=<scheme://host[:port]>
OPENCLAW_AUTH_TOKEN=<token>
OPENCLAW_AGENT_ID=openclaw/default
```

## 6) Validation sequence

1. From PortWorld runtime:

```bash
curl -i "$OPENCLAW_BASE_URL/v1/models" \
  -H "Authorization: Bearer $OPENCLAW_AUTH_TOKEN"
```

2. PortWorld checks:

```bash
portworld doctor --target local
```

3. Realtime delegation flow:

- call `delegate_to_openclaw`
- poll with `openclaw_task_status`
- optional cancel with `openclaw_task_cancel`

## 7) Trusted proxy mode caveat

`gateway.auth.mode=trusted-proxy` is valid only with an identity-aware reverse proxy and blocked direct gateway access.

Checklist:

1. Gateway not directly reachable from internet.
2. Only proxy-origin traffic can reach gateway.
3. Proxy injects verified identity headers.
4. Firewall and routing prevent header spoofing by untrusted clients.


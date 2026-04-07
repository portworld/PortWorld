#!/usr/bin/env bash
set -euo pipefail

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi

have_ss=true
if ! command -v ss >/dev/null 2>&1; then
  have_ss=false
fi

have_openclaw=true
if ! command -v openclaw >/dev/null 2>&1; then
  have_openclaw=false
fi

echo "== OpenClaw Gateway Discovery =="
echo "host: $(hostname)"
echo

if [[ "$have_openclaw" == "true" ]]; then
  echo "-- auth mode --"
  openclaw config get gateway.auth.mode 2>/dev/null || true
  echo
  echo "-- gateway token present --"
  token_value="$(openclaw config get gateway.auth.token 2>/dev/null | tail -n 1 || true)"
  if [[ -n "$token_value" && "$token_value" != "null" ]]; then
    echo "present: yes (redacted)"
  else
    echo "present: no"
  fi
  echo
else
  echo "warning: openclaw CLI not found on PATH; skipping config checks" >&2
fi

echo "-- service status (best effort) --"
systemctl --user status openclaw-gateway --no-pager >/tmp/openclaw-gateway-status.out 2>&1 || true
head -n 20 /tmp/openclaw-gateway-status.out || true
echo

echo "-- listeners --"
if [[ "$have_ss" == "true" ]]; then
  ss -ltnp | grep -Ei 'LISTEN|openclaw' || true
else
  echo "warning: ss not found; skipping listener dump" >&2
fi
echo

token_candidate="${OPENCLAW_GATEWAY_TOKEN:-${OPENCLAW_AUTH_TOKEN:-}}"
if [[ -z "$token_candidate" && "$have_openclaw" == "true" ]]; then
  token_candidate="$(openclaw config get gateway.auth.token 2>/dev/null | tail -n 1 || true)"
fi

if [[ -z "$token_candidate" || "$token_candidate" == "null" ]]; then
  echo "warning: no token found; set OPENCLAW_GATEWAY_TOKEN or OPENCLAW_AUTH_TOKEN to run authenticated probes" >&2
fi

ports="${OPENCLAW_PORT_CANDIDATES:-18789 18791 18792}"
echo "-- /v1/models probes --"
for p in $ports; do
  echo "=== $p ==="
  if [[ -n "$token_candidate" && "$token_candidate" != "null" ]]; then
    curl -sS -o /tmp/openclaw_probe_$p.out -w "HTTP %{http_code}\n" \
      -H "Authorization: Bearer $token_candidate" \
      "http://127.0.0.1:$p/v1/models" || true
  else
    curl -sS -o /tmp/openclaw_probe_$p.out -w "HTTP %{http_code}\n" \
      "http://127.0.0.1:$p/v1/models" || true
  fi
  head -c 220 /tmp/openclaw_probe_$p.out || true
  echo
  echo
done

echo "done."

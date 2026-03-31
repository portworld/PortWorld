#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(pwd)"
MODE="source"
STACK_NAME=""
SETUP_MODE="quickstart"

usage() {
  cat <<USAGE
Usage:
  bootstrap_portworld_cli.sh [--project-root PATH] [--mode source|published] [--stack-name NAME] [--setup-mode quickstart|manual]

Behavior:
  - Sync repo Python environment via uv
  - Initialize PortWorld CLI non-interactively with safe defaults
  - Run local doctor + status checks
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root)
      PROJECT_ROOT="$2"
      shift 2
      ;;
    --mode)
      MODE="$2"
      shift 2
      ;;
    --stack-name)
      STACK_NAME="$2"
      shift 2
      ;;
    --setup-mode)
      SETUP_MODE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "$MODE" != "source" && "$MODE" != "published" ]]; then
  echo "--mode must be source or published" >&2
  exit 2
fi
if [[ "$SETUP_MODE" != "quickstart" && "$SETUP_MODE" != "manual" ]]; then
  echo "--setup-mode must be quickstart or manual" >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but not found on PATH." >&2
  exit 2
fi

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  REALTIME_PROVIDER="openai"
  REALTIME_API_KEY="$OPENAI_API_KEY"
elif [[ -n "${GEMINI_LIVE_API_KEY:-}" ]]; then
  REALTIME_PROVIDER="gemini_live"
  REALTIME_API_KEY="$GEMINI_LIVE_API_KEY"
else
  echo "Missing realtime key. Set OPENAI_API_KEY or GEMINI_LIVE_API_KEY before bootstrap." >&2
  exit 2
fi

cd "$PROJECT_ROOT"
uv sync --quiet

INIT_CMD=(
  uv run python -m portworld_cli.main init
  --setup-mode "$SETUP_MODE"
  --runtime-source "$MODE"
  --project-mode local
  --without-vision
  --without-tooling
  --yes
  --non-interactive
  --realtime-provider "$REALTIME_PROVIDER"
  --realtime-api-key "$REALTIME_API_KEY"
)

if [[ "$MODE" == "published" && -n "$STACK_NAME" ]]; then
  INIT_CMD+=(--stack-name "$STACK_NAME")
fi

"${INIT_CMD[@]}"
uv run python -m portworld_cli.main doctor --target local
uv run python -m portworld_cli.main status

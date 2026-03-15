#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="armapidus"
REPO_NAME="PortWorld"
INSTALLER_URL="https://openclaw.ai/install.sh"
DEFAULT_RELEASE_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest"

PORTWORLD_VERSION="${PORTWORLD_VERSION:-latest}"
PORTWORLD_NO_INIT="${PORTWORLD_NO_INIT:-0}"
PORTWORLD_NON_INTERACTIVE="${PORTWORLD_NON_INTERACTIVE:-0}"
PORTWORLD_INSTALL_SOURCE_URL="${PORTWORLD_INSTALL_SOURCE_URL:-}"
PORTWORLD_RELEASE_API_URL="${PORTWORLD_RELEASE_API_URL:-$DEFAULT_RELEASE_API_URL}"

REQUESTED_VERSION="$PORTWORLD_VERSION"
NO_INIT="$PORTWORLD_NO_INIT"
NON_INTERACTIVE="$PORTWORLD_NON_INTERACTIVE"

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
  COLOR_INFO=$'\033[38;5;110m'
  COLOR_WARN=$'\033[38;5;214m'
  COLOR_ERROR=$'\033[38;5;203m'
  COLOR_SUCCESS=$'\033[38;5;78m'
  COLOR_ACCENT=$'\033[1;38;5;45m'
  COLOR_RESET=$'\033[0m'
else
  COLOR_INFO=""
  COLOR_WARN=""
  COLOR_ERROR=""
  COLOR_SUCCESS=""
  COLOR_ACCENT=""
  COLOR_RESET=""
fi

log_info() {
  printf '%s==>%s %s\n' "$COLOR_INFO" "$COLOR_RESET" "$*"
}

log_warn() {
  printf '%swarn:%s %s\n' "$COLOR_WARN" "$COLOR_RESET" "$*"
}

log_error() {
  printf '%serror:%s %s\n' "$COLOR_ERROR" "$COLOR_RESET" "$*" >&2
}

log_success() {
  printf '%s✓%s %s\n' "$COLOR_SUCCESS" "$COLOR_RESET" "$*"
}

section() {
  printf '\n%s%s%s\n' "$COLOR_ACCENT" "$*" "$COLOR_RESET"
}

fail() {
  log_error "$*"
  exit 1
}

print_usage() {
  cat <<EOF
PortWorld installer for macOS and Linux

Usage:
  curl -fsSL --proto '=https' --tlsv1.2 ${INSTALLER_URL} | bash
  curl -fsSL --proto '=https' --tlsv1.2 ${INSTALLER_URL} | bash -s -- [options]

Options:
  --help                 Show this help text.
  --version <tag|latest> Install a specific release tag such as v0.1.0, or latest.
  --no-init              Install the CLI without running portworld init.
  --non-interactive      Install only; do not attempt interactive setup.

Environment overrides:
  PORTWORLD_VERSION=<tag|latest>
  PORTWORLD_NO_INIT=1
  PORTWORLD_NON_INTERACTIVE=1

Internal/test overrides:
  PORTWORLD_INSTALL_SOURCE_URL=<path-or-url>
  PORTWORLD_RELEASE_API_URL=<url>
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h)
        print_usage
        exit 0
        ;;
      --version)
        [[ $# -ge 2 ]] || fail "--version requires a value"
        REQUESTED_VERSION="$2"
        shift 2
        ;;
      --no-init)
        NO_INIT=1
        shift
        ;;
      --non-interactive)
        NON_INTERACTIVE=1
        shift
        ;;
      *)
        fail "Unknown argument: $1"
        ;;
    esac
  done
}

require_command() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1 || fail "Missing required command: $name"
}

ensure_supported_os() {
  case "$(uname -s 2>/dev/null || true)" in
    Darwin|Linux)
      ;;
    *)
      fail "Unsupported operating system. This installer supports macOS and Linux only."
      ;;
  esac
}

curl_get() {
  local url="$1"
  curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --silent \
    --show-error \
    --location \
    --retry 3 \
    --retry-delay 1 \
    --connect-timeout 10 \
    --max-time 30 \
    "$url"
}

ensure_python_version() {
  python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    print(
        f"Python 3.11 or newer is required; found {sys.version.split()[0]}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

ensure_python_pip() {
  if python3 -m pip --version >/dev/null 2>&1; then
    return
  fi
  log_info "pip was not available through python3; trying ensurepip"
  python3 -m ensurepip --upgrade >/dev/null 2>&1 || fail \
    "python3 is present, but pip could not be bootstrapped. Install pip for python3 and retry."
  python3 -m pip --version >/dev/null 2>&1 || fail \
    "python3 is present, but pip is still unavailable after ensurepip."
}

ensure_pipx() {
  if python3 -m pipx --version >/dev/null 2>&1; then
    return
  fi
  log_info "pipx was not available; installing it with python3 -m pip --user"
  ensure_python_pip
  python3 -m pip install --user pipx >/dev/null
  python3 -m pipx ensurepath >/dev/null || true
  python3 -m pipx --version >/dev/null 2>&1 || fail "pipx installation did not succeed."
}

resolve_pipx_bin_dir() {
  local pipx_bin_dir
  pipx_bin_dir="$(python3 -m pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
  if [[ -n "$pipx_bin_dir" ]]; then
    printf '%s\n' "$pipx_bin_dir"
    return
  fi

  python3 - <<'PY'
import os
import site
print(os.path.join(site.USER_BASE, "bin"))
PY
}

ensure_portworld_on_path() {
  local pipx_bin_dir
  pipx_bin_dir="$(resolve_pipx_bin_dir)"
  if [[ -d "$pipx_bin_dir" ]]; then
    export PATH="$pipx_bin_dir:$PATH"
  fi
  command -v portworld >/dev/null 2>&1 || fail \
    "portworld was installed but is not on PATH. Open a new shell or add the pipx bin dir to PATH and retry."
}

normalize_tag() {
  local value="$1"
  if [[ "$value" == "latest" ]]; then
    printf '%s\n' "$value"
    return
  fi
  if [[ "$value" == v* ]]; then
    printf '%s\n' "$value"
    return
  fi
  printf 'v%s\n' "$value"
}

resolve_latest_release_tag() {
  local raw_json
  raw_json="$(curl_get "$PORTWORLD_RELEASE_API_URL")" || return 1
  PORTWORLD_RELEASE_PAYLOAD="$raw_json" python3 -c '
import json
import os

raw = os.environ.get("PORTWORLD_RELEASE_PAYLOAD", "").strip()
if not raw:
    raise SystemExit(1)
payload = json.loads(raw)
tag = payload.get("tag_name")
if not isinstance(tag, str) or not tag.strip():
    raise SystemExit(1)
print(tag.strip())
'
}

build_archive_url() {
  local tag="$1"
  printf 'https://github.com/%s/%s/archive/refs/tags/%s.zip\n' "$REPO_OWNER" "$REPO_NAME" "$tag"
}

resolve_install_source() {
  if [[ -n "$PORTWORLD_INSTALL_SOURCE_URL" ]]; then
    RESOLVED_VERSION="custom"
    INSTALL_SOURCE="$PORTWORLD_INSTALL_SOURCE_URL"
    return
  fi

  local requested
  requested="$(normalize_tag "$REQUESTED_VERSION")"
  if [[ "$requested" == "latest" ]]; then
    local latest_tag
    latest_tag="$(resolve_latest_release_tag)" || fail \
      "Unable to resolve the latest PortWorld release tag from GitHub Releases."
    RESOLVED_VERSION="$latest_tag"
    INSTALL_SOURCE="$(build_archive_url "$latest_tag")"
    return
  fi

  RESOLVED_VERSION="$requested"
  INSTALL_SOURCE="$(build_archive_url "$requested")"
}

run_install() {
  section "Installing PortWorld CLI"
  log_info "Source: $INSTALL_SOURCE"
  if [[ "$RESOLVED_VERSION" != "custom" ]]; then
    log_info "Version: $RESOLVED_VERSION"
  fi
  python3 -m pipx install --force "$INSTALL_SOURCE"
  ensure_portworld_on_path
  portworld --version >/dev/null
  log_success "PortWorld CLI installed"
}

run_init() {
  if [[ "$NO_INIT" == "1" ]]; then
    log_info "Skipping portworld init because --no-init was set"
    log_info "Next step: portworld init"
    return
  fi

  if [[ "$NON_INTERACTIVE" == "1" ]]; then
    log_info "Skipping portworld init because --non-interactive was set"
    log_info "Next step: portworld init"
    return
  fi

  if [[ -r /dev/tty && -w /dev/tty ]]; then
    section "Launching setup"
    if portworld init </dev/tty >/dev/tty 2>&1; then
      log_success "PortWorld CLI installed and initialized"
      return
    fi
    local init_status=$?
    log_warn "PortWorld CLI was installed, but 'portworld init' did not complete successfully"
    log_info "Re-run: portworld init"
    exit "$init_status"
  fi

  log_info "PortWorld CLI installed successfully"
  log_info "No interactive terminal was available, so setup was not started automatically"
  log_info "Next step: portworld init"
}

main() {
  parse_args "$@"

  section "Preparing installer"
  ensure_supported_os
  require_command bash
  require_command curl
  require_command python3
  ensure_python_version || fail "Python 3.11 or newer is required."
  ensure_pipx
  resolve_install_source
  run_install
  run_init
}

main "$@"

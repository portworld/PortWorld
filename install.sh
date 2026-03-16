#!/usr/bin/env bash
set -euo pipefail

REPO_OWNER="portworld"
REPO_NAME="PortWorld"
INSTALLER_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/main/install.sh"
DEFAULT_RELEASE_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"
UV_INSTALL_DIR="${UV_INSTALL_DIR:-$HOME/.local/bin}"
PYPI_PACKAGE_NAME="${PORTWORLD_PYPI_PACKAGE:-portworld}"
PYPI_PACKAGE_FALLBACK_NAME="${PORTWORLD_PYPI_PACKAGE_FALLBACK:-portworld-cli}"
MINIMUM_PYTHON_VERSION="3.11"

PORTWORLD_VERSION="${PORTWORLD_VERSION:-latest}"
PORTWORLD_NO_INIT="${PORTWORLD_NO_INIT:-0}"
PORTWORLD_NON_INTERACTIVE="${PORTWORLD_NON_INTERACTIVE:-0}"
PORTWORLD_INSTALL_SOURCE_URL="${PORTWORLD_INSTALL_SOURCE_URL:-}"
PORTWORLD_RELEASE_API_URL="${PORTWORLD_RELEASE_API_URL:-$DEFAULT_RELEASE_API_URL}"

REQUESTED_VERSION="$PORTWORLD_VERSION"
NO_INIT="$PORTWORLD_NO_INIT"
NON_INTERACTIVE="$PORTWORLD_NON_INTERACTIVE"
CURRENT_OS=""
UV_BIN=""
SELECTED_PYTHON=""
USE_MANAGED_PYTHON=0
INSTALL_TARGET=""
RESOLVED_TAG=""
RESOLVED_VERSION=""
INSTALL_SOURCE_DESCRIPTION=""

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

This bootstrap installs uv automatically when needed. If Python 3.11+ is not
available locally, it installs a managed Python runtime before installing the CLI.
After install, the default interactive onboarding path is the operator-friendly
zero-clone workspace flow. Contributor/source-checkout setup remains available
from within `portworld init`.

Options:
  --help                 Show this help text.
  --version <tag|latest> Install a specific release tag such as v0.1.0, or latest.
  --no-init              Install the CLI without running portworld init.
  --non-interactive      Install only; do not attempt interactive setup.

Environment overrides:
  PORTWORLD_VERSION=<tag|latest>
  PORTWORLD_NO_INIT=1
  PORTWORLD_NON_INTERACTIVE=1
  PORTWORLD_PYPI_PACKAGE=<name>

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

prepend_path_dir() {
  local dir="$1"
  [[ -n "$dir" ]] || return 0
  [[ -d "$dir" ]] || return 0
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) export PATH="$dir:$PATH" ;;
  esac
}

ensure_supported_os() {
  case "$(uname -s 2>/dev/null || true)" in
    Darwin)
      CURRENT_OS="macos"
      ;;
    Linux)
      CURRENT_OS="linux"
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

python_version_string() {
  python3 - <<'PY'
import sys
print(sys.version.split()[0])
PY
}

python_meets_minimum() {
  python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
}

run_selected_python() {
  "$SELECTED_PYTHON" "$@"
}

ensure_uv() {
  UV_BIN="$(command -v uv || true)"
  if [[ -n "$UV_BIN" ]]; then
    prepend_path_dir "$UV_INSTALL_DIR"
    return
  fi

  section "Bootstrapping uv"
  mkdir -p "$UV_INSTALL_DIR"
  curl_get "$UV_INSTALLER_URL" | env UV_INSTALL_DIR="$UV_INSTALL_DIR" UV_NO_MODIFY_PATH=1 sh || fail \
    "Unable to install uv via the official installer."
  prepend_path_dir "$UV_INSTALL_DIR"
  UV_BIN="$(command -v uv || true)"
  [[ -n "$UV_BIN" ]] || fail \
    "uv was installed but is not on PATH. Open a new shell or run: export PATH=\"$UV_INSTALL_DIR:\$PATH\""
  log_success "uv installed"
}

ensure_python_runtime() {
  if command -v python3 >/dev/null 2>&1 && python_meets_minimum; then
    SELECTED_PYTHON="python3"
    USE_MANAGED_PYTHON=0
    log_info "Using system Python $(python_version_string)"
    return
  fi

  section "Installing Python"
  if command -v python3 >/dev/null 2>&1; then
    log_info "System Python $(python_version_string) is too old; installing managed Python ${MINIMUM_PYTHON_VERSION}"
  else
    log_info "python3 was not found; installing managed Python ${MINIMUM_PYTHON_VERSION}"
  fi

  "$UV_BIN" python install "$MINIMUM_PYTHON_VERSION" || fail \
    "Unable to install managed Python ${MINIMUM_PYTHON_VERSION} with uv."
  prepend_path_dir "$UV_INSTALL_DIR"
  SELECTED_PYTHON="$(command -v "python${MINIMUM_PYTHON_VERSION}" || true)"
  [[ -n "$SELECTED_PYTHON" ]] || fail \
    "Managed Python ${MINIMUM_PYTHON_VERSION} was installed, but its executable was not found on PATH."
  USE_MANAGED_PYTHON=1
  log_success "Managed Python ${MINIMUM_PYTHON_VERSION} installed"
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

version_from_tag() {
  local tag="$1"
  local normalized="${tag#v}"
  [[ "$normalized" =~ ^[0-9]+(\.[0-9]+)*$ ]] || fail \
    "Release tag '$tag' does not map to a valid Python package version."
  printf '%s\n' "$normalized"
}

resolve_latest_release_tag() {
  local raw_json
  raw_json="$(curl_get "$PORTWORLD_RELEASE_API_URL")" || return 1
  PORTWORLD_RELEASE_PAYLOAD="$raw_json" run_selected_python -c '
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

resolve_install_target() {
  if [[ -n "$PORTWORLD_INSTALL_SOURCE_URL" ]]; then
    INSTALL_TARGET="$PORTWORLD_INSTALL_SOURCE_URL"
    INSTALL_SOURCE_DESCRIPTION="override"
    RESOLVED_TAG="custom"
    RESOLVED_VERSION="custom"
    return
  fi

  local requested
  requested="$(normalize_tag "$REQUESTED_VERSION")"
  if [[ "$requested" == "latest" ]]; then
    RESOLVED_TAG="$(resolve_latest_release_tag)" || fail \
      "Unable to resolve the latest PortWorld release tag from GitHub Releases."
  else
    RESOLVED_TAG="$requested"
  fi
  RESOLVED_VERSION="$(version_from_tag "$RESOLVED_TAG")"
  INSTALL_TARGET="${PYPI_PACKAGE_NAME}==${RESOLVED_VERSION}"
  INSTALL_SOURCE_DESCRIPTION="PyPI"
}

ensure_portworld_on_path() {
  local uv_tool_bin_dir
  uv_tool_bin_dir="$("$UV_BIN" tool dir --bin 2>/dev/null || true)"
  if [[ -z "$uv_tool_bin_dir" ]]; then
    uv_tool_bin_dir="$HOME/.local/bin"
  fi
  prepend_path_dir "$uv_tool_bin_dir"
  command -v portworld >/dev/null 2>&1 || fail \
    "portworld was installed but is not on PATH. Open a new shell or run: export PATH=\"$uv_tool_bin_dir:\$PATH\""
}

run_install() {
  local -a install_args

  section "Installing PortWorld CLI"
  if [[ "$INSTALL_SOURCE_DESCRIPTION" == "override" ]]; then
    log_info "Install source override: $INSTALL_TARGET"
  else
    log_info "Source: $INSTALL_SOURCE_DESCRIPTION"
    log_info "Release tag: $RESOLVED_TAG"
    log_info "Package version: $RESOLVED_VERSION"
  fi
  log_info "PyPI package name: $PYPI_PACKAGE_NAME (fallback: $PYPI_PACKAGE_FALLBACK_NAME)"

  install_args=("$UV_BIN" tool install --force)
  if [[ "$USE_MANAGED_PYTHON" == "1" ]]; then
    install_args+=(--managed-python --python "$SELECTED_PYTHON")
  else
    install_args+=(--python "$SELECTED_PYTHON" --no-python-downloads)
  fi

  if [[ "$INSTALL_SOURCE_DESCRIPTION" == "override" && -d "$INSTALL_TARGET" ]]; then
    install_args+=(--editable "$INSTALL_TARGET")
  else
    install_args+=("$INSTALL_TARGET")
  fi

  "${install_args[@]}"
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
    log_info "portworld init will offer the operator workspace flow by default and keep source checkout setup available."
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
  ensure_uv
  ensure_python_runtime
  resolve_install_target
  run_install
  run_init
}

main "$@"

#!/usr/bin/env bash
set -euo pipefail

INSTALL_SOURCE_URL="https://github.com/armapidus/PortWorld/archive/refs/heads/main.zip"
INSTALL_COMMAND=(python3 -m pipx install --force "$INSTALL_SOURCE_URL")
INIT_COMMAND=(portworld init)

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    fail "Missing required command: $name"
  fi
}

ensure_supported_os() {
  case "$(uname -s)" in
    Darwin|Linux)
      ;;
    *)
      fail "Unsupported operating system. Phase E supports macOS and Linux only."
      ;;
  esac
}

ensure_python_pip() {
  if python3 -m pip --version >/dev/null 2>&1; then
    return
  fi
  log "pip was not available through python3; trying ensurepip."
  if ! python3 -m ensurepip --upgrade >/dev/null 2>&1; then
    fail "python3 is present, but pip could not be bootstrapped. Install pip for python3 and retry."
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    fail "python3 is present, but pip is still unavailable after ensurepip."
  fi
}

ensure_pipx() {
  if python3 -m pipx --version >/dev/null 2>&1; then
    return
  fi
  log "pipx was not available; installing it with python3 -m pip --user."
  ensure_python_pip
  python3 -m pip install --user pipx
  python3 -m pipx ensurepath >/dev/null
  if ! python3 -m pipx --version >/dev/null 2>&1; then
    fail "pipx installation did not succeed."
  fi
}

resolve_pipx_bin_dir() {
  local pipx_bin_dir
  pipx_bin_dir="$(python3 -m pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
  if [ -n "$pipx_bin_dir" ]; then
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
  if [ -d "$pipx_bin_dir" ]; then
    export PATH="$pipx_bin_dir:$PATH"
  fi
  if ! command -v portworld >/dev/null 2>&1; then
    fail "portworld was installed but is not on PATH. Open a new shell or add the pipx bin dir to PATH and retry."
  fi
}

run_install() {
  log "Installing PortWorld CLI with pipx."
  "${INSTALL_COMMAND[@]}"
  ensure_portworld_on_path
  portworld --version >/dev/null
}

run_init() {
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    log "Launching portworld init."
    if "${INIT_COMMAND[@]}" </dev/tty >/dev/tty 2>&1; then
      log "PortWorld CLI installed and initialized."
      return
    else
      local init_status=$?
      log "PortWorld CLI was installed, but 'portworld init' did not complete successfully."
      log "Re-run: portworld init"
      exit "$init_status"
    fi
  fi

  log "PortWorld CLI installed successfully."
  log "No interactive terminal was available, so setup was not started automatically."
  log "Next step: portworld init"
}

main() {
  ensure_supported_os
  require_command bash
  require_command curl
  require_command python3
  ensure_pipx
  run_install
  run_init
}

main "$@"

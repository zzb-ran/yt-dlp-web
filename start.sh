#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PROVIDER_SERVER_DIR="${ROOT_DIR}/tools/bgutil-ytdlp-pot-provider/server"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON_BIN=""

log() {
  printf '[ytfetch] %s\n' "$1"
}

fail() {
  printf '[ytfetch] %s\n' "$1" >&2
  exit 1
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

python_version_ok() {
  local cmd="$1"
  "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

find_python_bin() {
  local candidate
  for candidate in python3.12 python3 python; do
    if have_cmd "$candidate" && python_version_ok "$candidate"; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  done
  return 1
}

use_sudo() {
  if [[ "${EUID}" -ne 0 ]]; then
    if have_cmd sudo; then
      sudo "$@"
    else
      fail "sudo is required to install system dependencies automatically: $*"
    fi
  else
    "$@"
  fi
}

refresh_shell_path() {
  if have_cmd brew; then
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null || brew shellenv)"
  fi
}

install_homebrew_if_needed() {
  if have_cmd brew; then
    return
  fi
  log "Homebrew not found, installing it now"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  refresh_shell_path
  have_cmd brew || fail "Homebrew installation failed. Please install it manually and retry."
}

ensure_python_macos() {
  if find_python_bin; then
    return
  fi
  install_homebrew_if_needed
  log "Installing Python 3.12, Node.js, ffmpeg, Deno, and Git"
  brew install python@3.12 node ffmpeg deno git
}

ensure_linux_deps() {
  if have_cmd apt-get; then
    use_sudo apt-get update
    use_sudo apt-get install -y python3.12 python3.12-venv python3-pip git curl ffmpeg nodejs npm
  elif have_cmd dnf; then
    use_sudo dnf install -y python3.12 python3.12-pip git curl ffmpeg nodejs npm
  elif have_cmd yum; then
    use_sudo yum install -y python3.12 python3-pip git curl ffmpeg nodejs npm
  elif have_cmd pacman; then
    use_sudo pacman -Sy --noconfirm python python-pip git curl ffmpeg nodejs npm
  else
    fail "Unsupported Linux package manager. Supported: apt, dnf, yum, pacman."
  fi

  if ! have_cmd deno; then
    log "Installing Deno"
    curl -fsSL https://deno.land/install.sh | sh
    export DENO_INSTALL="${HOME}/.deno"
    export PATH="${DENO_INSTALL}/bin:${PATH}"
  fi
}

ensure_node_version() {
  have_cmd node || fail "Node.js was not found. Install Node.js 20+ and retry."
  have_cmd npm || fail "npm was not found. Reinstall Node.js and retry."
  local major
  major="$(node -p "process.versions.node.split('.')[0]")"
  if [[ "${major}" -lt 20 ]]; then
    fail "Node.js $(node --version) is too old. Version 20+ is required."
  fi
}

ensure_python_version() {
  find_python_bin || fail "Python 3.12+ was not found. Please install it and retry."
}

ensure_system_deps() {
  local os_name
  os_name="$(uname -s)"
  case "${os_name}" in
    Darwin)
      ensure_python_macos
      refresh_shell_path
      ;;
    Linux)
      ensure_linux_deps
      ;;
    *)
      fail "This script supports only macOS and Linux. Use start.ps1 or start.cmd on Windows."
      ;;
  esac

  refresh_shell_path
  ensure_python_version
  ensure_node_version
  have_cmd ffmpeg || fail "ffmpeg was not found. Install it and retry."
  have_cmd git || fail "Git was not found. Install it and retry."
  have_cmd deno || fail "Deno was not found. Install it and retry."
  [[ -d "${PROVIDER_SERVER_DIR}" ]] || fail "Provider directory is missing: ${PROVIDER_SERVER_DIR}"
}

create_venv_if_needed() {
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "Creating Python virtual environment with ${PYTHON_BIN}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
}

install_python_deps() {
  log "Installing Python dependencies"
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
  "${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"
}

install_provider_server() {
  log "Installing and building the bgutil provider server"
  (cd "${PROVIDER_SERVER_DIR}" && npm ci && npx tsc)
}

start_server() {
  log "Starting app at http://${HOST}:${PORT}"
  exec "${VENV_DIR}/bin/uvicorn" app.main:app --host "${HOST}" --port "${PORT}"
}

main() {
  cd "${ROOT_DIR}"
  ensure_system_deps
  create_venv_if_needed
  install_python_deps
  install_provider_server
  start_server
}

main "$@"

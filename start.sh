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
      fail "缺少 sudo，无法自动安装系统依赖: $*"
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
  log "未检测到 Homebrew，开始安装"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  refresh_shell_path
  have_cmd brew || fail "Homebrew 安装失败，请手动安装后重试"
}

ensure_python_macos() {
  if find_python_bin; then
    return
  fi
  install_homebrew_if_needed
  log "安装 Python 3.12 / Node / ffmpeg / deno / git"
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
    fail "当前 Linux 发行版未检测到受支持的包管理器（apt/dnf/yum/pacman）"
  fi

  if ! have_cmd deno; then
    log "安装 Deno"
    curl -fsSL https://deno.land/install.sh | sh
    export DENO_INSTALL="${HOME}/.deno"
    export PATH="${DENO_INSTALL}/bin:${PATH}"
  fi
}

ensure_node_version() {
  have_cmd node || fail "未检测到 node，请先安装 Node.js >= 20"
  have_cmd npm || fail "未检测到 npm，请重新安装 Node.js"
  local major
  major="$(node -p "process.versions.node.split('.')[0]")"
  if [[ "${major}" -lt 20 ]]; then
    fail "当前 Node.js 版本过低（$(node --version)），需要 >= 20"
  fi
}

ensure_python_version() {
  find_python_bin || fail "未检测到可用的 Python 3.12+，请安装后重试"
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
      fail "当前脚本只支持 macOS / Linux，Windows 请使用 start.ps1 或 start.cmd"
      ;;
  esac

  refresh_shell_path
  ensure_python_version
  ensure_node_version
  have_cmd ffmpeg || fail "缺少 ffmpeg，请安装后重试"
  have_cmd git || fail "缺少 git，请安装后重试"
  have_cmd deno || fail "缺少 deno，请安装后重试"
  [[ -d "${PROVIDER_SERVER_DIR}" ]] || fail "缺少 provider 目录: ${PROVIDER_SERVER_DIR}"
}

create_venv_if_needed() {
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    log "创建 Python 虚拟环境（${PYTHON_BIN}）"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
}

install_python_deps() {
  log "安装 Python 依赖"
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
  "${VENV_DIR}/bin/pip" install -r "${ROOT_DIR}/requirements.txt"
}

install_provider_server() {
  log "安装并编译 bgutil provider server"
  (cd "${PROVIDER_SERVER_DIR}" && npm ci && npx tsc)
}

start_server() {
  log "启动服务 http://${HOST}:${PORT}"
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

#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
VENV_DIR = ROOT_DIR / ".venv"
PROVIDER_SERVER_DIR = ROOT_DIR / "tools" / "bgutil-ytdlp-pot-provider" / "server"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = os.environ.get("PORT", "8000")


def log(message: str) -> None:
    print(f"[ytfetch] {message}")


def fail(message: str) -> None:
    raise SystemExit(f"[ytfetch] {message}")


def have_cmd(command: str) -> bool:
    return shutil.which(command) is not None


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def run_capture(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def ensure_supported_python() -> None:
    if sys.version_info < (3, 12):
        fail("`start.py` 需要 Python 3.12+。如果当前机器还没有 Python，请先用 `start.sh` 或 `start.ps1` 完成首次引导。")


def use_sudo_prefix() -> list[str]:
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
        if have_cmd("sudo"):
            return ["sudo"]
        fail("缺少 sudo，无法自动安装系统依赖")
    return []


def ensure_homebrew() -> None:
    if have_cmd("brew"):
        return
    log("未检测到 Homebrew，开始安装")
    run(
        [
            "/bin/bash",
            "-c",
            "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)",
        ],
        env={**os.environ, "NONINTERACTIVE": "1"},
    )


def ensure_macos_deps() -> None:
    if not have_cmd("brew"):
        ensure_homebrew()

    missing = []
    if not have_cmd("node"):
        missing.append("node")
    if not have_cmd("ffmpeg"):
        missing.append("ffmpeg")
    if not have_cmd("deno"):
        missing.append("deno")
    if not have_cmd("git"):
        missing.append("git")

    if missing:
        log(f"安装依赖: {' '.join(missing)}")
        run(["brew", "install", *missing])


def ensure_linux_deps() -> None:
    sudo = use_sudo_prefix()
    if have_cmd("apt-get"):
        run([*sudo, "apt-get", "update"])
        run([*sudo, "apt-get", "install", "-y", "git", "curl", "ffmpeg", "nodejs", "npm"])
    elif have_cmd("dnf"):
        run([*sudo, "dnf", "install", "-y", "git", "curl", "ffmpeg", "nodejs", "npm"])
    elif have_cmd("yum"):
        run([*sudo, "yum", "install", "-y", "git", "curl", "ffmpeg", "nodejs", "npm"])
    elif have_cmd("pacman"):
        run([*sudo, "pacman", "-Sy", "--noconfirm", "git", "curl", "ffmpeg", "nodejs", "npm"])
    else:
        fail("当前 Linux 发行版未检测到受支持的包管理器（apt/dnf/yum/pacman）")

    if not have_cmd("deno"):
        log("安装 Deno")
        run(["/bin/sh", "-c", "curl -fsSL https://deno.land/install.sh | sh"])
        deno_bin = Path.home() / ".deno" / "bin"
        os.environ["PATH"] = f"{deno_bin}{os.pathsep}{os.environ['PATH']}"


def ensure_windows_deps() -> None:
    if not have_cmd("winget"):
        fail("未检测到 winget，无法在 Windows 自动安装依赖")

    packages = [
        ("git", "Git.Git", "Git"),
        ("node", "OpenJS.NodeJS.LTS", "Node.js LTS"),
        ("ffmpeg", "Gyan.FFmpeg", "FFmpeg"),
        ("deno", "DenoLand.Deno", "Deno"),
    ]
    for command, package_id, display_name in packages:
        if have_cmd(command):
            continue
        log(f"安装 {display_name}")
        run(
            [
                "winget",
                "install",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "-e",
                "--id",
                package_id,
            ]
        )

    machine_path = os.environ.get("PATH", "")
    user_machine = os.environ.get("Path", "")
    os.environ["PATH"] = f"{machine_path}{os.pathsep}{user_machine}"


def ensure_system_deps() -> None:
    system = platform.system()
    if system == "Darwin":
        ensure_macos_deps()
    elif system == "Linux":
        ensure_linux_deps()
    elif system == "Windows":
        ensure_windows_deps()
    else:
        fail(f"不支持的平台: {system}")

    if not have_cmd("git"):
        fail("缺少 git，请安装后重试")
    if not have_cmd("ffmpeg"):
        fail("缺少 ffmpeg，请安装后重试")
    if not have_cmd("node") or not have_cmd("npm"):
        fail("缺少 Node.js / npm，请安装 Node.js >= 20")
    if not have_cmd("deno"):
        fail("缺少 deno，请安装后重试")
    if not PROVIDER_SERVER_DIR.is_dir():
        fail(f"缺少 provider 目录: {PROVIDER_SERVER_DIR}")

    major = int(run_capture(["node", "-p", "process.versions.node.split('.')[0]"]))
    if major < 20:
        fail(f"当前 Node.js 版本过低（{run_capture(['node', '--version'])}），需要 >= 20")


def ensure_venv() -> tuple[Path, Path, Path]:
    if platform.system() == "Windows":
        python_path = VENV_DIR / "Scripts" / "python.exe"
        pip_path = VENV_DIR / "Scripts" / "pip.exe"
        uvicorn_path = VENV_DIR / "Scripts" / "uvicorn.exe"
    else:
        python_path = VENV_DIR / "bin" / "python"
        pip_path = VENV_DIR / "bin" / "pip"
        uvicorn_path = VENV_DIR / "bin" / "uvicorn"

    if not python_path.exists():
        log(f"创建 Python 虚拟环境: {VENV_DIR}")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])

    return python_path, pip_path, uvicorn_path


def install_python_deps(python_path: Path, pip_path: Path) -> None:
    log("安装 Python 依赖")
    run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(pip_path), "install", "-r", str(ROOT_DIR / "requirements.txt")])


def install_provider_server() -> None:
    log("安装并编译 bgutil provider server")
    run(["npm", "ci"], cwd=PROVIDER_SERVER_DIR)
    run(["npx", "tsc"], cwd=PROVIDER_SERVER_DIR)


def start_server(uvicorn_path: Path) -> None:
    log(f"启动服务 http://{HOST}:{PORT}")
    os.execv(str(uvicorn_path), [str(uvicorn_path), "app.main:app", "--host", HOST, "--port", PORT])


def main() -> None:
    os.chdir(ROOT_DIR)
    ensure_supported_python()
    ensure_system_deps()
    python_path, pip_path, uvicorn_path = ensure_venv()
    install_python_deps(python_path, pip_path)
    install_provider_server()
    start_server(uvicorn_path)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        fail(f"命令执行失败: {' '.join(exc.cmd)}")

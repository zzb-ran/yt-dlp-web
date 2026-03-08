from __future__ import annotations

import contextlib
import importlib.metadata
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

PROVIDER_PORT = 4416
PROVIDER_URL = f"http://127.0.0.1:{PROVIDER_PORT}"
PROVIDER_SERVER_DIR = Path(__file__).resolve().parent.parent / "tools" / "bgutil-ytdlp-pot-provider" / "server"
PROVIDER_ENTRYPOINT = PROVIDER_SERVER_DIR / "build" / "main.js"

_provider_process: Optional[subprocess.Popen[str]] = None


def get_provider_plugin_version() -> Optional[str]:
    with contextlib.suppress(importlib.metadata.PackageNotFoundError):
        return importlib.metadata.version("bgutil-ytdlp-pot-provider")
    return None


def is_provider_plugin_installed() -> bool:
    return get_provider_plugin_version() is not None


def is_provider_server_reachable(timeout: float = 0.5) -> bool:
    try:
        with urlopen(f"{PROVIDER_URL}/ping", timeout=timeout) as response:
            return response.status == 200
    except URLError:
        return False


def ensure_provider_server() -> bool:
    global _provider_process

    if is_provider_server_reachable():
        return True

    if not PROVIDER_ENTRYPOINT.exists():
        return False

    if _provider_process and _provider_process.poll() is None:
        return _wait_for_provider()

    _provider_process = subprocess.Popen(
        ["node", str(PROVIDER_ENTRYPOINT), "--port", str(PROVIDER_PORT)],
        cwd=PROVIDER_SERVER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return _wait_for_provider()


def stop_provider_server() -> None:
    global _provider_process

    if _provider_process and _provider_process.poll() is None:
        _provider_process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            _provider_process.wait(timeout=3)
        if _provider_process.poll() is None:
            _provider_process.kill()
    _provider_process = None


def is_provider_ready() -> bool:
    return is_provider_plugin_installed() and is_provider_server_reachable()


def _wait_for_provider() -> bool:
    deadline = time.time() + 8
    while time.time() < deadline:
        if is_provider_server_reachable():
            return True
        time.sleep(0.25)
    return False

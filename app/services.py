from __future__ import annotations

import contextlib
import os
import re
import shutil
import secrets
import shutil as shell_shutil
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import yt_dlp
from fastapi import HTTPException
from yt_dlp.cookies import CookieLoadError, extract_cookies_from_browser
from yt_dlp.extractor import list_extractors

from .models import DownloadOption, EnvironmentResponse, EnvironmentStatusItem, ResolveRequest
from .provider import get_provider_plugin_version, is_provider_ready, is_provider_server_reachable

SUPPORTED_BROWSERS = (
    ("chrome", "Chrome"),
    ("chromium", "Chromium"),
    ("edge", "Microsoft Edge"),
    ("firefox", "Firefox"),
    ("safari", "Safari"),
    ("brave", "Brave"),
)

OUTPUT_DIR = Path(tempfile.gettempdir()) / "ytfetch-downloads"
AUTH_CACHE_DIR = Path(tempfile.gettempdir()) / "ytfetch-auth-cache"
YTDLP_CACHE_DIR = Path(tempfile.gettempdir()) / "ytfetch-yt-dlp-cache"
AUTH_TTL_SECONDS = 30 * 60


class ExtractionError(RuntimeError):
    """Raised when yt-dlp cannot process the request."""


@dataclass
class AuthContext:
    cookie_file: Optional[str] = None
    browser: Optional[str] = None


@contextlib.contextmanager
def build_auth_context(request: ResolveRequest) -> Iterator[AuthContext]:
    cleanup_expired_auth_tokens()

    if request.auth_token:
        cached_cookie_file = get_cached_cookie_file(request.auth_token)
        if not cached_cookie_file:
            raise HTTPException(status_code=400, detail="认证缓存已过期，请重新解析一次")
        yield AuthContext(cookie_file=str(cached_cookie_file))
        return

    cookie_file: Optional[str] = None
    try:
        if request.cookie_source == "text":
            if not request.cookie_text or not request.cookie_text.strip():
                raise HTTPException(status_code=400, detail="已选择 cookies 文本模式，但没有提供 cookies 内容")
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write(request.cookie_text.strip())
                cookie_file = handle.name
            yield AuthContext(cookie_file=cookie_file)
            return

        if request.cookie_source == "browser":
            if not request.browser:
                raise HTTPException(status_code=400, detail="已选择浏览器 cookies 模式，但没有提供浏览器类型")
            yield AuthContext(browser=request.browser)
            return

        yield AuthContext()
    finally:
        if cookie_file:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(cookie_file)


def cache_browser_cookies(browser: str) -> str:
    cleanup_expired_auth_tokens()
    AUTH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    token = browser_cache_token(browser)
    cached = get_cached_cookie_file(token)
    if cached:
        return token
    cookie_path = AUTH_CACHE_DIR / f"{token}.txt"
    try:
        jar = extract_cookies_from_browser(browser_name=browser)
        jar.save(str(cookie_path))
    except CookieLoadError as exc:
        raise HTTPException(status_code=400, detail=f"读取浏览器 cookies 失败：{exc}") from exc
    except Exception as exc:  # pragma: no cover - upstream exception types vary
        raise HTTPException(status_code=400, detail=f"读取浏览器 cookies 失败：{exc}") from exc
    return token


def browser_cache_token(browser: str) -> str:
    sanitized = re.sub(r"[^a-z0-9]+", "-", browser.lower()).strip("-")
    return f"browser-{sanitized or secrets.token_hex(4)}"


def get_cached_cookie_file(token: str) -> Optional[Path]:
    cookie_path = AUTH_CACHE_DIR / f"{token}.txt"
    if not cookie_path.exists():
        return None
    if time.time() - cookie_path.stat().st_mtime > AUTH_TTL_SECONDS:
        with contextlib.suppress(FileNotFoundError):
            cookie_path.unlink()
        return None
    return cookie_path


def cleanup_expired_auth_tokens() -> None:
    if not AUTH_CACHE_DIR.exists():
        return
    cutoff = time.time() - AUTH_TTL_SECONDS
    for item in AUTH_CACHE_DIR.glob("*.txt"):
        if item.stat().st_mtime < cutoff:
            with contextlib.suppress(FileNotFoundError):
                item.unlink()


def ydl_options(auth: AuthContext, download: bool = False, output_template: Optional[str] = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": not download,
        "cachedir": str(YTDLP_CACHE_DIR),
        "ignoreconfig": True,
        "remote_components": ["ejs:github"],
    }
    if auth.cookie_file:
        opts["cookiefile"] = auth.cookie_file
    if auth.browser:
        opts["cookiesfrombrowser"] = (auth.browser,)
    if download and output_template:
        opts["outtmpl"] = {"default": output_template}
        opts["restrictfilenames"] = False
        opts["noprogress"] = True
    return opts


def youtube_extractor_args(request: ResolveRequest, strategy: str = "default") -> dict[str, Any]:
    if strategy == "youtube_android_public":
        return {"youtube": {"player_client": ["android"]}}
    return {}


def extract_media_info(request: ResolveRequest) -> dict[str, Any]:
    with build_auth_context(request) as auth:
        try:
            opts = {**ydl_options(auth), "listformats": True}
            extractor_args = youtube_extractor_args(request)
            if extractor_args:
                opts["extractor_args"] = extractor_args
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(str(request.url), download=False)
        except Exception as exc:  # pragma: no cover - upstream exception types vary
            raise ExtractionError(str(exc)) from exc


def extract_public_youtube_info(url: str) -> dict[str, Any]:
    try:
        with yt_dlp.YoutubeDL(
            {
                **ydl_options(AuthContext()),
                "listformats": True,
                "extractor_args": {"youtube": {"player_client": ["android"]}},
            }
        ) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:  # pragma: no cover - upstream exception types vary
        raise ExtractionError(str(exc)) from exc


def select_download_options(
    info: dict[str, Any],
    *,
    strategy: str = "default",
    downloadable_override: Optional[bool] = None,
    disabled_reason: Optional[str] = None,
) -> list[DownloadOption]:
    formats = info.get("formats") or []
    best_audio = None
    audio_only: list[dict[str, Any]] = []
    mux_candidates: dict[tuple[Optional[int], str], dict[str, Any]] = {}
    progressive_candidates: dict[tuple[Optional[int], str], dict[str, Any]] = {}

    for fmt in formats:
        if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
            audio_only.append(fmt)
            if best_audio is None or (fmt.get("abr") or 0) > (best_audio.get("abr") or 0):
                best_audio = fmt
            continue

        if fmt.get("vcodec") == "none":
            continue

        height = fmt.get("height")
        ext = fmt.get("ext") or "mp4"
        key = (height, ext)
        has_audio = fmt.get("acodec") != "none"
        current = progressive_candidates if has_audio else mux_candidates
        existing = current.get(key)

        score = (
            fmt.get("fps") or 0,
            fmt.get("tbr") or 0,
            fmt.get("filesize") or fmt.get("filesize_approx") or 0,
        )
        existing_score = (
            (existing.get("fps") or 0),
            (existing.get("tbr") or 0),
            (existing.get("filesize") or existing.get("filesize_approx") or 0),
        ) if existing else (-1, -1, -1)
        if score > existing_score:
            current[key] = fmt

    options: list[DownloadOption] = []

    for (height, ext), fmt in sorted(progressive_candidates.items(), key=_video_sort_key, reverse=True):
        options.append(
            DownloadOption(
                key=f"prog-{fmt['format_id']}",
                label=_video_label(height, ext, merged=True),
                selector=fmt["format_id"],
                ext=ext,
                resolution=f"{height}p" if height else None,
                fps=fmt.get("fps"),
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                note="单文件直下",
                protocol=fmt.get("protocol"),
                delivery=_delivery_type(fmt),
                strategy=strategy,
                downloadable=_resolve_downloadable(fmt, downloadable_override),
                disabled_reason=disabled_reason,
            )
        )

    if best_audio:
        for (height, ext), fmt in sorted(mux_candidates.items(), key=_video_sort_key, reverse=True):
            selector = f"{fmt['format_id']}+{best_audio['format_id']}"
            options.append(
                DownloadOption(
                    key=f"mux-{fmt['format_id']}-{best_audio['format_id']}",
                    label=_video_label(height, ext, merged=False),
                    selector=selector,
                    ext=ext,
                    resolution=f"{height}p" if height else None,
                    fps=fmt.get("fps"),
                    filesize=(fmt.get("filesize") or fmt.get("filesize_approx") or 0)
                    + (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0),
                    note="视频 + 最佳音频",
                    protocol=fmt.get("protocol"),
                    delivery=_delivery_type(fmt),
                    strategy=strategy,
                    downloadable=_resolve_downloadable(fmt, downloadable_override),
                    disabled_reason=disabled_reason,
                )
            )

    best_audio_formats = sorted(audio_only, key=lambda item: ((item.get("abr") or 0), item.get("tbr") or 0), reverse=True)[:4]
    for fmt in best_audio_formats:
        abr = fmt.get("abr")
        ext = fmt.get("ext") or "m4a"
        options.append(
            DownloadOption(
                key=f"audio-{fmt['format_id']}",
                label=f"音频 {int(abr) if abr else '?'}kbps ({ext.upper()})",
                selector=fmt["format_id"],
                ext=ext,
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                note="仅音频",
                kind="audio",
                protocol=fmt.get("protocol"),
                delivery=_delivery_type(fmt),
                strategy=strategy,
                downloadable=_resolve_downloadable(fmt, downloadable_override),
                disabled_reason=disabled_reason,
            )
        )

    seen: set[str] = set()
    deduped: list[DownloadOption] = []
    for item in options:
        if item.selector in seen:
            continue
        seen.add(item.selector)
        deduped.append(item)

    return deduped


def perform_download(
    request: ResolveRequest,
    selector: str,
    filename_hint: Optional[str] = None,
    strategy: str = "default",
    progress_hook: Optional[Callable[[dict[str, Any]], None]] = None,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_dir = Path(tempfile.mkdtemp(prefix="ytfetch-", dir=OUTPUT_DIR))
    output_template = str(target_dir / "%(title).180B [%(id)s].%(ext)s")

    with build_auth_context(request) as auth:
        try:
            extractor_args = youtube_extractor_args(request, strategy=strategy)
            with yt_dlp.YoutubeDL(
                {
                    **ydl_options(auth, download=True, output_template=output_template),
                    "format": selector,
                    **({"merge_output_format": "mp4"} if "+" in selector else {}),
                    **({"extractor_args": extractor_args} if extractor_args else {}),
                    **({"progress_hooks": [progress_hook], "noprogress": False} if progress_hook else {}),
                }
            ) as ydl:
                info = ydl.extract_info(str(request.url), download=True)
                downloaded = Path(ydl.prepare_filename(info))
                if not downloaded.exists():
                    candidates = sorted(target_dir.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True)
                    if not candidates:
                        raise ExtractionError("下载完成但没有找到输出文件")
                    downloaded = candidates[0]

                if filename_hint:
                    safe_name = sanitize_filename(filename_hint)
                    renamed = downloaded.with_name(f"{safe_name}{downloaded.suffix}")
                    downloaded.rename(renamed)
                    downloaded = renamed
                return downloaded
        except Exception as exc:  # pragma: no cover - upstream exception types vary
            shutil.rmtree(target_dir, ignore_errors=True)
            raise ExtractionError(str(exc)) from exc


def sanitize_filename(value: str) -> str:
    sanitized = re.sub(r"[^\w\-. ]+", "_", value, flags=re.ASCII).strip()
    return sanitized[:120] or "download"


def _video_sort_key(item: tuple[tuple[Optional[int], str], dict[str, Any]]) -> tuple[int, int]:
    (height, _ext), _fmt = item
    return (height or 0, 1)


def _video_label(height: Optional[int], ext: str, merged: bool) -> str:
    resolution = f"{height}p" if height else "原始质量"
    suffix = "完整视频" if merged else "高清合并"
    return f"{resolution} ({ext.upper()}) {suffix}"


def _delivery_type(fmt: dict[str, Any]) -> str:
    protocol = (fmt.get("protocol") or "").lower()
    if "m3u8" in protocol or "dash" in protocol or "mpd" in protocol:
        return "stream"
    return "direct"


def _resolve_downloadable(fmt: dict[str, Any], downloadable_override: Optional[bool]) -> bool:
    if downloadable_override is not None:
        return downloadable_override
    return _delivery_type(fmt) == "direct"


def merge_download_options(primary: list[DownloadOption], secondary: list[DownloadOption]) -> list[DownloadOption]:
    merged: list[DownloadOption] = []
    seen: set[tuple[str, str]] = set()

    for item in [*primary, *secondary]:
        key = (item.kind, item.resolution or item.label)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def get_environment_status() -> EnvironmentResponse:
    extractors = list(list_extractors())
    extractor_count = len(extractors)
    runtime = [
        EnvironmentStatusItem(
            label="Deno",
            available=bool(shell_shutil.which("deno")),
            detail="YouTube 新版解析建议安装，用于处理站点 JS 挑战",
        ),
        EnvironmentStatusItem(
            label="ffmpeg",
            available=bool(shell_shutil.which("ffmpeg")),
            detail="用于合并视频和音频，以及处理部分流媒体格式",
        ),
        EnvironmentStatusItem(
            label="Cookies 缓存",
            available=True,
            detail=f"已启用，同一条任务 {AUTH_TTL_SECONDS // 60} 分钟内复用认证，避免重复输密码",
        ),
        EnvironmentStatusItem(
            label="PO Token Provider",
            available=is_provider_ready(),
            detail=_provider_detail(),
        ),
        EnvironmentStatusItem(
            label="yt-dlp 平台支持",
            available=True,
            detail=f"当前版本内置约 {extractor_count} 个 extractor，可覆盖绝大多数 yt-dlp 支持站点",
        ),
    ]
    return EnvironmentResponse(
        runtime=runtime,
        support_summary="支持所有当前 yt-dlp 可解析平台，不只 YouTube。输入任意站点链接后会自动匹配对应 extractor。",
        extractor_count=extractor_count,
        featured_platforms=["YouTube", "Bilibili", "X/Twitter", "TikTok", "Instagram", "Facebook", "Twitch", "SoundCloud"],
    )


def _provider_detail() -> str:
    version = get_provider_plugin_version()
    if not version:
        return "未安装 bgutil-ytdlp-pot-provider 插件"
    if is_provider_server_reachable():
        return f"插件 {version} 已安装，provider server 已连接，高分辨率 YouTube 将自动尝试授权"
    return f"插件 {version} 已安装，但 provider server 未连接"

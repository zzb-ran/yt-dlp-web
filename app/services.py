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
COOKIE_RECOMMENDED_EXTRACTORS = {
    "BiliBili",
    "Facebook",
    "Instagram",
    "TikTok",
    "Twitter",
    "TikTokUser",
    "TwitchVod",
}


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
            raise HTTPException(status_code=400, detail="Cached authentication has expired. Resolve the link again.")
        yield AuthContext(cookie_file=str(cached_cookie_file))
        return

    cookie_file: Optional[str] = None
    try:
        if request.cookie_source == "text":
            if not request.cookie_text or not request.cookie_text.strip():
                raise HTTPException(status_code=400, detail="Cookie text mode was selected, but no cookies.txt content was provided.")
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
                handle.write(request.cookie_text.strip())
                cookie_file = handle.name
            yield AuthContext(cookie_file=cookie_file)
            return

        if request.cookie_source == "browser":
            if not request.browser:
                raise HTTPException(status_code=400, detail="Browser cookie mode was selected, but no browser type was provided.")
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
        raise HTTPException(status_code=400, detail=f"Failed to read browser cookies: {exc}") from exc
    except Exception as exc:  # pragma: no cover - upstream exception types vary
        raise HTTPException(status_code=400, detail=f"Failed to read browser cookies: {exc}") from exc
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
    ffmpeg_available = bool(shell_shutil.which("ffmpeg"))
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
        downloadable, item_disabled_reason = _resolve_downloadability(
            fmt,
            downloadable_override=downloadable_override,
            requires_ffmpeg=False,
            ffmpeg_available=ffmpeg_available,
            disabled_reason=disabled_reason,
        )
        options.append(
            DownloadOption(
                key=f"prog-{fmt['format_id']}",
                label=_video_label(height, ext, merged=True),
                selector=fmt["format_id"],
                ext=ext,
                resolution=f"{height}p" if height else None,
                fps=fmt.get("fps"),
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                note="Single-file direct download",
                protocol=fmt.get("protocol"),
                delivery=_delivery_type(fmt),
                delivery_label=_delivery_label(fmt),
                strategy=strategy,
                downloadable=downloadable,
                disabled_reason=item_disabled_reason,
            )
        )

    if best_audio:
        for (height, ext), fmt in sorted(mux_candidates.items(), key=_video_sort_key, reverse=True):
            selector = f"{fmt['format_id']}+{best_audio['format_id']}"
            downloadable, item_disabled_reason = _resolve_downloadability(
                fmt,
                downloadable_override=downloadable_override,
                requires_ffmpeg=True,
                ffmpeg_available=ffmpeg_available,
                disabled_reason=disabled_reason,
            )
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
                    note="Video + best audio",
                    protocol=fmt.get("protocol"),
                    delivery=_delivery_type(fmt),
                    delivery_label=_delivery_label(fmt),
                    strategy=strategy,
                    downloadable=downloadable,
                    disabled_reason=item_disabled_reason,
                )
            )

    best_audio_formats = sorted(audio_only, key=lambda item: ((item.get("abr") or 0), item.get("tbr") or 0), reverse=True)[:4]
    for fmt in best_audio_formats:
        abr = fmt.get("abr")
        ext = fmt.get("ext") or "m4a"
        downloadable, item_disabled_reason = _resolve_downloadability(
            fmt,
            downloadable_override=downloadable_override,
            requires_ffmpeg=False,
            ffmpeg_available=ffmpeg_available,
            disabled_reason=disabled_reason,
        )
        options.append(
            DownloadOption(
                key=f"audio-{fmt['format_id']}",
                label=f"Audio {int(abr) if abr else '?'}kbps ({ext.upper()})",
                selector=fmt["format_id"],
                ext=ext,
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                note="Audio only",
                kind="audio",
                protocol=fmt.get("protocol"),
                delivery=_delivery_type(fmt),
                delivery_label=_delivery_label(fmt),
                strategy=strategy,
                downloadable=downloadable,
                disabled_reason=item_disabled_reason,
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
                        raise ExtractionError("Download finished but no output file was found")
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
    resolution = f"{height}p" if height else "Source quality"
    suffix = "Full video" if merged else "Muxed"
    return f"{resolution} ({ext.upper()}) {suffix}"


def _delivery_type(fmt: dict[str, Any]) -> str:
    protocol = (fmt.get("protocol") or "").lower()
    if "m3u8" in protocol or "dash" in protocol or "mpd" in protocol:
        return "stream"
    return "direct"


def _delivery_label(fmt: dict[str, Any]) -> str:
    protocol = (fmt.get("protocol") or "").lower()
    if "m3u8" in protocol:
        return "HLS m3u8"
    if "mpd" in protocol or "dash" in protocol:
        return "DASH / MPD"
    if protocol:
        return "Direct"
    return "Downloadable"


def _resolve_downloadability(
    fmt: dict[str, Any],
    *,
    downloadable_override: Optional[bool],
    requires_ffmpeg: bool,
    ffmpeg_available: bool,
    disabled_reason: Optional[str],
) -> tuple[bool, Optional[str]]:
    if disabled_reason:
        return False, disabled_reason

    if downloadable_override is not None:
        if downloadable_override and requires_ffmpeg and not ffmpeg_available:
            return False, "This format requires ffmpeg for muxing. Install ffmpeg and retry."
        if downloadable_override and _delivery_type(fmt) == "stream" and not ffmpeg_available:
            return False, "This streaming format requires ffmpeg for stable downloads. Install ffmpeg and retry."
        return downloadable_override, None if downloadable_override else "This format is currently unavailable for download."

    if requires_ffmpeg and not ffmpeg_available:
        return False, "This format requires ffmpeg for muxing. Install ffmpeg and retry."

    if _delivery_type(fmt) == "stream" and not ffmpeg_available:
        return False, "This streaming format requires ffmpeg for stable downloads. Install ffmpeg and retry."

    return True, None


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


def build_platform_warnings(
    info: dict[str, Any],
    request: ResolveRequest,
    *,
    provider_ready: bool = False,
) -> list[str]:
    warnings: list[str] = []
    extractor_key = str(info.get("extractor_key") or "")
    formats = info.get("formats") or []
    has_stream_formats = any(_delivery_type(fmt) == "stream" for fmt in formats)
    ffmpeg_available = bool(shell_shutil.which("ffmpeg"))

    if extractor_key == "Youtube":
        if provider_ready:
            warnings.append("PO Token Provider is connected. High-resolution YouTube formats will attempt automatic authorization.")
        else:
            warnings.append("PO Token Provider is not connected. Only stable YouTube formats that do not require extra authorization will be offered.")

    if request.cookie_source == "none" and extractor_key in COOKIE_RECOMMENDED_EXTRACTORS:
        warnings.append(f"{extractor_key} content such as member-only, age-restricted, higher-quality, or geo-restricted media usually works better with browser cookies.")

    if has_stream_formats:
        if ffmpeg_available:
            warnings.append("This site exposes HLS / DASH streaming formats. ffmpeg will be used for downloading and merging when needed.")
        else:
            warnings.append("This site exposes HLS / DASH streaming formats. Without ffmpeg, some formats will be disabled.")

    if request.cookie_source == "browser" and request.auth_token:
        warnings.append("Authentication has been cached. Downloads after this resolve step will not prompt for the browser keychain password again.")

    return warnings


def get_environment_status() -> EnvironmentResponse:
    extractors = list(list_extractors())
    extractor_count = len(extractors)
    runtime = [
        EnvironmentStatusItem(
            label="Deno",
            available=bool(shell_shutil.which("deno")),
            detail="Recommended for newer YouTube extraction flows and site-side JavaScript challenges",
        ),
        EnvironmentStatusItem(
            label="ffmpeg",
            available=bool(shell_shutil.which("ffmpeg")),
            detail="Used for muxing audio/video and handling some streaming formats",
        ),
        EnvironmentStatusItem(
            label="Cookie cache",
            available=True,
            detail=f"Enabled. Authentication is reused for {AUTH_TTL_SECONDS // 60} minutes to avoid repeated password prompts.",
        ),
        EnvironmentStatusItem(
            label="PO Token Provider",
            available=is_provider_ready(),
            detail=_provider_detail(),
        ),
        EnvironmentStatusItem(
            label="yt-dlp extractor coverage",
            available=True,
            detail=f"This version ships with about {extractor_count} extractors and covers most sites supported by yt-dlp.",
        ),
    ]
    return EnvironmentResponse(
        runtime=runtime,
        support_summary="Supports all platforms currently handled by yt-dlp, not just YouTube. The correct extractor is selected automatically from the URL.",
        extractor_count=extractor_count,
        featured_platforms=["YouTube", "Bilibili", "X/Twitter", "TikTok", "Instagram", "Facebook", "Twitch", "SoundCloud"],
    )


def _provider_detail() -> str:
    version = get_provider_plugin_version()
    if not version:
        return "bgutil-ytdlp-pot-provider is not installed"
    if is_provider_server_reachable():
        return f"Plugin {version} is installed, the provider server is connected, and high-resolution YouTube authorization will be attempted automatically."
    return f"Plugin {version} is installed, but the provider server is not connected."

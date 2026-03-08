from __future__ import annotations

import math
import shutil
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .models import (
    AuthBrowserOption,
    AuthCapabilitiesResponse,
    DownloadRequest,
    DownloadJobResponse,
    EnvironmentResponse,
    ResolveRequest,
    ResolveResponse,
    VideoSummary,
)
from .provider import ensure_provider_server, is_provider_ready, stop_provider_server
from .services import ExtractionError, SUPPORTED_BROWSERS, build_platform_warnings, extract_media_info, extract_public_youtube_info, get_environment_status, merge_download_options, perform_download, select_download_options
from .services import cache_browser_cookies, cleanup_expired_auth_tokens

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_JOBS: dict[str, dict[str, Any]] = {}
DOWNLOAD_JOBS_LOCK = threading.Lock()

app = FastAPI(title="yt-dlp Fetch", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/thumbnail")
def thumbnail_proxy(
    url: str = Query(min_length=1),
    referer: str | None = Query(default=None),
) -> Response:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Unsupported thumbnail URL protocol")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )
    }
    if referer:
        referer_parsed = urlparse(referer)
        if referer_parsed.scheme in {"http", "https"}:
            headers["Referer"] = referer

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=12) as upstream:
            content_type = upstream.headers.get_content_type() or "image/jpeg"
            body = upstream.read()
            return Response(content=body, media_type=content_type)
    except (HTTPError, URLError) as exc:
        raise HTTPException(status_code=502, detail=f"Thumbnail fetch failed: {exc}") from exc


@app.get("/api/auth/capabilities", response_model=AuthCapabilitiesResponse)
def auth_capabilities() -> AuthCapabilitiesResponse:
    return AuthCapabilitiesResponse(
        oauth_message="As of 2026-03-08, yt-dlp documents that YouTube OAuth is currently unusable because of site-side restrictions. Use browser cookies instead.",
        browsers=[AuthBrowserOption(value=value, label=label) for value, label in SUPPORTED_BROWSERS],
    )


@app.get("/api/environment", response_model=EnvironmentResponse)
def environment() -> EnvironmentResponse:
    return get_environment_status()


@app.post("/api/resolve", response_model=ResolveResponse)
def resolve_video(payload: ResolveRequest) -> ResolveResponse:
    provider_ready = False
    if "youtube.com" in str(payload.url) or "youtu.be" in str(payload.url):
        provider_ready = ensure_provider_server()

    auth_token = payload.auth_token
    if payload.cookie_source == "browser" and not auth_token:
        auth_token = cache_browser_cookies(payload.browser or "")
        payload = payload.model_copy(update={"auth_token": auth_token})

    try:
        info = extract_media_info(payload)
    except ExtractionError as exc:
        message = str(exc)
        if "Requested format is not available" in message:
            message = "Format discovery was affected by local yt-dlp config or site-side format changes. Local config is already ignored here; refresh and retry. If it still fails, install Deno and try another browser cookie source."
        raise HTTPException(status_code=400, detail=f"Resolve failed: {message}") from exc

    formats = select_download_options(
        info,
        downloadable_override=True if info.get("extractor_key") == "Youtube" and provider_ready else None,
    )
    if info.get("extractor_key") == "Youtube":
        if provider_ready:
            try:
                public_info = extract_public_youtube_info(str(payload.url))
                public_formats = select_download_options(public_info, strategy="youtube_android_public")
                direct_public = [item for item in public_formats if item.downloadable]
                provider_formats = [item for item in formats if item.kind == "video"]
                audio_items = [item for item in formats if item.kind == "audio"]
                formats = merge_download_options(provider_formats + direct_public + audio_items, [])
            except ExtractionError:
                pass
        else:
            try:
                public_info = extract_public_youtube_info(str(payload.url))
                public_formats = select_download_options(public_info, strategy="youtube_android_public")
                direct_public = [item for item in public_formats if item.downloadable]
                if direct_public:
                    restricted = [
                        item.model_copy(
                            update={
                                "downloadable": False,
                                "disabled_reason": "This YouTube quality currently requires an additional PO Token or provider and is temporarily unavailable for direct download.",
                            }
                        )
                        for item in formats
                        if item.kind == "video" and not item.downloadable
                    ]
                    audio_items = [item for item in formats if item.kind == "audio"]
                    formats = merge_download_options(direct_public + audio_items, restricted)
            except ExtractionError:
                pass
    if not formats:
        raise HTTPException(status_code=400, detail="No downloadable formats were found")

    warning_payload = payload.model_copy(update={"auth_token": auth_token})
    warnings = build_platform_warnings(info, warning_payload, provider_ready=provider_ready)

    return ResolveResponse(
        video=VideoSummary(
            title=info.get("title") or "Untitled",
            uploader=info.get("uploader"),
            duration=_coerce_duration(info.get("duration")),
            thumbnail=info.get("thumbnail"),
            webpage_url=info.get("webpage_url"),
            extractor=info.get("extractor_key"),
            platform=info.get("extractor") or info.get("extractor_key"),
        ),
        formats=formats,
        warnings=warnings,
        auth_token=auth_token,
    )


@app.post("/api/download")
def download_video(payload: DownloadRequest) -> FileResponse:
    try:
        request_payload = payload
        if payload.strategy == "youtube_android_public":
            request_payload = payload.model_copy(update={"cookie_source": "none", "browser": None, "cookie_text": None, "auth_token": None})
        downloaded = perform_download(request_payload, payload.format_selector, payload.filename_hint, strategy=payload.strategy)
    except ExtractionError as exc:
        message = str(exc)
        if "Requested format is not available" in message:
            message = "The selected format is no longer available for download. This is usually caused by site-side format changes, missing cookies, or a missing ffmpeg/Deno dependency. Resolve again and choose another format."
        raise HTTPException(status_code=400, detail=f"Download failed: {message}") from exc

    return FileResponse(
        downloaded,
        filename=downloaded.name,
        media_type="application/octet-stream",
        background=BackgroundTask(lambda: shutil.rmtree(downloaded.parent, ignore_errors=True)),
    )


@app.post("/api/download-jobs", response_model=DownloadJobResponse)
def create_download_job(payload: DownloadRequest) -> DownloadJobResponse:
    job_id = uuid4().hex
    with DOWNLOAD_JOBS_LOCK:
        DOWNLOAD_JOBS[job_id] = {
            "status": "queued",
            "progress": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": None,
            "speed": None,
            "eta": None,
            "filename": None,
            "error": None,
            "file_path": None,
        }

    thread = threading.Thread(target=_run_download_job, args=(job_id, payload), daemon=True)
    thread.start()
    return _job_response(job_id)


@app.get("/api/download-jobs/{job_id}", response_model=DownloadJobResponse)
def get_download_job(job_id: str) -> DownloadJobResponse:
    return _job_response(job_id)


@app.get("/api/download-jobs/{job_id}/file")
def get_download_job_file(job_id: str) -> FileResponse:
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Download job was not found")
        if job["status"] != "completed" or not job["file_path"]:
            raise HTTPException(status_code=409, detail="Downloaded file is not ready yet")
        file_path = Path(job["file_path"])

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Downloaded file no longer exists")

    return FileResponse(file_path, filename=file_path.name, media_type="application/octet-stream")


def _run_download_job(job_id: str, payload: DownloadRequest) -> None:
    def progress_hook(status: dict[str, Any]) -> None:
        total_bytes = status.get("total_bytes") or status.get("total_bytes_estimate")
        downloaded_bytes = status.get("downloaded_bytes") or 0
        progress = (downloaded_bytes / total_bytes * 100) if total_bytes else 0.0
        with DOWNLOAD_JOBS_LOCK:
            job = DOWNLOAD_JOBS.get(job_id)
            if not job:
                return
            if status.get("status") == "finished":
                job.update(
                    {
                        "status": "downloading",
                        "progress": 100.0,
                        "downloaded_bytes": downloaded_bytes or total_bytes,
                        "total_bytes": total_bytes,
                        "speed": status.get("speed"),
                        "eta": 0,
                    }
                )
                return

            job.update(
                {
                    "status": "downloading",
                    "progress": progress,
                    "downloaded_bytes": downloaded_bytes,
                    "total_bytes": total_bytes,
                    "speed": status.get("speed"),
                    "eta": status.get("eta"),
                }
            )

    try:
        request_payload = payload
        if payload.strategy == "youtube_android_public":
            request_payload = payload.model_copy(update={"cookie_source": "none", "browser": None, "cookie_text": None, "auth_token": None})
        downloaded = perform_download(
            request_payload,
            payload.format_selector,
            payload.filename_hint,
            strategy=payload.strategy,
            progress_hook=progress_hook,
        )
        with DOWNLOAD_JOBS_LOCK:
            job = DOWNLOAD_JOBS[job_id]
            job.update(
                {
                    "status": "completed",
                    "progress": 100.0,
                    "filename": downloaded.name,
                    "file_path": str(downloaded),
                }
            )
    except HTTPException as exc:
        with DOWNLOAD_JOBS_LOCK:
            job = DOWNLOAD_JOBS[job_id]
            job.update({"status": "failed", "error": exc.detail})
    except ExtractionError as exc:
        with DOWNLOAD_JOBS_LOCK:
            job = DOWNLOAD_JOBS[job_id]
            job.update({"status": "failed", "error": str(exc)})


def _job_response(job_id: str) -> DownloadJobResponse:
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Download job was not found")
        return DownloadJobResponse(job_id=job_id, **{key: value for key, value in job.items() if key != "file_path"})


def _coerce_duration(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(math.floor(value)))
    try:
        return max(0, int(math.floor(float(value))))
    except (TypeError, ValueError):
        return None


@app.on_event("shutdown")
def cleanup_downloads() -> None:
    from .services import AUTH_CACHE_DIR, OUTPUT_DIR

    downloads_dir = OUTPUT_DIR
    if downloads_dir.exists():
        shutil.rmtree(downloads_dir, ignore_errors=True)
    cleanup_expired_auth_tokens()
    auth_cache_dir = AUTH_CACHE_DIR
    if auth_cache_dir.exists():
        shutil.rmtree(auth_cache_dir, ignore_errors=True)
    with DOWNLOAD_JOBS_LOCK:
        for job in DOWNLOAD_JOBS.values():
            file_path = job.get("file_path")
            if file_path:
                path = Path(file_path)
                if path.exists():
                    shutil.rmtree(path.parent, ignore_errors=True)
        DOWNLOAD_JOBS.clear()
    stop_provider_server()


@app.on_event("startup")
def startup_services() -> None:
    ensure_provider_server()

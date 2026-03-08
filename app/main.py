from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from .services import ExtractionError, SUPPORTED_BROWSERS, extract_media_info, extract_public_youtube_info, get_environment_status, merge_download_options, perform_download, select_download_options
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


@app.get("/api/auth/capabilities", response_model=AuthCapabilitiesResponse)
def auth_capabilities() -> AuthCapabilitiesResponse:
    return AuthCapabilitiesResponse(
        oauth_message="截至 2026-03-08，yt-dlp 官方已说明 YouTube OAuth 因站点限制不可用，请改用浏览器 cookies。",
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
            message = "解析阶段被本机 yt-dlp 配置或站点返回格式影响。服务现已忽略本机配置，请刷新页面后重试；如果仍失败，先安装 Deno，再尝试切换 cookies 浏览器。"
        raise HTTPException(status_code=400, detail=f"解析失败：{message}") from exc

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
                                "disabled_reason": "当前 YouTube 这档清晰度需要额外 PO Token 或 provider，暂时不提供直下",
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
        raise HTTPException(status_code=400, detail="没有找到可用的下载格式")

    warnings: list[str] = []
    if info.get("extractor_key") == "Youtube":
        if provider_ready:
            warnings.append("PO Token Provider 已连接，YouTube 高分辨率会自动尝试授权。")
        else:
            warnings.append("PO Token Provider 当前未连接，因此仅会稳定提供无需额外授权的 YouTube 格式。")
    if auth_token:
        warnings.append("当前认证已暂存，本次解析后下载不会再次读取浏览器密码。")

    return ResolveResponse(
        video=VideoSummary(
            title=info.get("title") or "Untitled",
            uploader=info.get("uploader"),
            duration=info.get("duration"),
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
            message = "当前清晰度在重新下载时不可用，通常是站点格式变化、缺少 cookies，或本机缺少 ffmpeg / Deno。请重新解析后换一个格式再试。"
        raise HTTPException(status_code=400, detail=f"下载失败：{message}") from exc

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
            raise HTTPException(status_code=404, detail="下载任务不存在")
        if job["status"] != "completed" or not job["file_path"]:
            raise HTTPException(status_code=409, detail="文件尚未准备好")
        file_path = Path(job["file_path"])

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="下载文件不存在")

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
            raise HTTPException(status_code=404, detail="下载任务不存在")
        return DownloadJobResponse(job_id=job_id, **{key: value for key, value in job.items() if key != "file_path"})


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

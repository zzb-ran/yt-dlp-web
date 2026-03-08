from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class ResolveRequest(BaseModel):
    url: HttpUrl
    cookie_source: Literal["none", "browser", "text"] = "none"
    browser: Optional[str] = None
    cookie_text: Optional[str] = None
    auth_token: Optional[str] = None


class DownloadRequest(ResolveRequest):
    format_selector: str = Field(min_length=1)
    filename_hint: Optional[str] = None
    strategy: Literal["default", "youtube_android_public"] = "default"


class DownloadOption(BaseModel):
    key: str
    label: str
    selector: str
    ext: str
    resolution: Optional[str] = None
    fps: Optional[float] = None
    filesize: Optional[int] = None
    note: Optional[str] = None
    kind: Literal["video", "audio"] = "video"
    protocol: Optional[str] = None
    delivery: Literal["direct", "stream"] = "direct"
    delivery_label: Optional[str] = None
    strategy: Literal["default", "youtube_android_public"] = "default"
    downloadable: bool = True
    disabled_reason: Optional[str] = None


class VideoSummary(BaseModel):
    title: str
    uploader: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    webpage_url: Optional[str] = None
    extractor: Optional[str] = None
    platform: Optional[str] = None


class ResolveResponse(BaseModel):
    video: VideoSummary
    formats: list[DownloadOption]
    warnings: list[str] = Field(default_factory=list)
    auth_token: Optional[str] = None


class AuthBrowserOption(BaseModel):
    value: str
    label: str


class AuthCapabilitiesResponse(BaseModel):
    oauth_supported: bool = False
    oauth_message: str
    browser_cookie_supported: bool = True
    browsers: list[AuthBrowserOption]


class EnvironmentStatusItem(BaseModel):
    label: str
    available: bool
    detail: str


class EnvironmentResponse(BaseModel):
    runtime: list[EnvironmentStatusItem]
    support_summary: str
    extractor_count: int
    featured_platforms: list[str]


class DownloadJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "downloading", "completed", "failed"]
    progress: float = 0
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    speed: Optional[float] = None
    eta: Optional[int] = None
    filename: Optional[str] = None
    error: Optional[str] = None

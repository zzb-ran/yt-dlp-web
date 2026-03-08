const form = document.getElementById("fetch-form");
const statusNode = document.getElementById("status");
const warningsNode = document.getElementById("warnings");
const resultNode = document.getElementById("result");
const resolveButton = document.getElementById("resolve-button");
const cookieSource = document.getElementById("cookie_source");
const browserField = document.getElementById("browser-field");
const cookieTextField = document.getElementById("cookie-text-field");
const browserSelect = document.getElementById("browser");
const oauthTip = document.getElementById("oauth-tip");
const thumbnail = document.getElementById("thumbnail");
const videoTitle = document.getElementById("video-title");
const videoSubtitle = document.getElementById("video-subtitle");
const runtimeGrid = document.getElementById("runtime-grid");
const supportSummary = document.getElementById("support-summary");
const platformChips = document.getElementById("platform-chips");
const videoTags = document.getElementById("video-tags");
const videoFormatGrid = document.getElementById("video-format-grid");
const audioFormatGrid = document.getElementById("audio-format-grid");
const progressPanel = document.getElementById("download-progress");
const progressStatusText = document.getElementById("progress-status-text");
const progressBarFill = document.getElementById("progress-bar-fill");
const progressPercent = document.getElementById("progress-percent");
const progressDetail = document.getElementById("progress-detail");
const AUTH_CACHE_KEY = "ytfetch_auth_cache";

let lastPayload = null;
let activeDownloadPoll = null;

async function parseApiResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  return { detail: text || `Request failed (${response.status})` };
}

function setStatus(message) {
  statusNode.textContent = message || "";
}

function resetProgress() {
  progressPanel.classList.add("hidden");
  progressPanel.classList.remove("is-complete");
  progressStatusText.textContent = "";
  progressBarFill.style.width = "0%";
  progressPercent.textContent = "0%";
  progressDetail.textContent = "Waiting for download";
}

function updateProgress(job) {
  progressPanel.classList.remove("hidden");
  progressPanel.classList.toggle("is-complete", job.status === "completed");
  const percent = Math.max(0, Math.min(100, job.progress || 0));
  progressBarFill.style.width = `${percent}%`;
  progressPercent.textContent = `${percent.toFixed(percent >= 100 ? 0 : 1)}%`;
  progressStatusText.textContent = ({
    queued: "Job created, waiting to start",
    downloading: "Downloading",
    completed: "Download complete, file is ready",
    failed: "Download failed",
  })[job.status] || "Processing";

  const parts = [];
  if (job.status === "completed" && job.filename) {
    parts.push(`File ${job.filename}`);
  }
  if (job.downloaded_bytes) {
    parts.push(humanSize(job.downloaded_bytes));
  }
  if (job.total_bytes) {
    parts.push(`Total ${humanSize(job.total_bytes)}`);
  }
  if (job.speed) {
    parts.push(`${humanSize(job.speed)}/s`);
  }
  if (Number.isFinite(job.eta)) {
    parts.push(`ETA ${formatEta(job.eta)}`);
  }
  if (job.error) {
    parts.push(job.error);
  }
  progressDetail.textContent = parts.join(" · ") || "Waiting for download";
}

function setWarnings(messages = []) {
  warningsNode.innerHTML = "";
  messages.forEach((message) => {
    const item = document.createElement("p");
    item.className = "warning";
    item.textContent = message;
    warningsNode.appendChild(item);
  });
}

function toggleAuthFields() {
  const source = cookieSource.value;
  browserField.classList.toggle("hidden", source !== "browser");
  cookieTextField.classList.toggle("hidden", source !== "text");
}

function readAuthCache() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_CACHE_KEY) || "null");
  } catch (_error) {
    return null;
  }
}

function writeAuthCache(data) {
  localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify(data));
}

function clearAuthCache() {
  localStorage.removeItem(AUTH_CACHE_KEY);
}

async function loadCapabilities() {
  const response = await fetch("/api/auth/capabilities");
  const data = await response.json();
  oauthTip.textContent = data.oauth_message;

  browserSelect.innerHTML = "";
  data.browsers.forEach((browser) => {
    const option = document.createElement("option");
    option.value = browser.value;
    option.textContent = browser.label;
    browserSelect.appendChild(option);
  });
}

async function loadEnvironment() {
  const response = await fetch("/api/environment");
  const data = await response.json();

  supportSummary.textContent = data.support_summary;
  runtimeGrid.innerHTML = "";
  data.runtime.forEach((item) => {
    const card = document.createElement("div");
    card.className = "runtime-card";
    card.dataset.available = item.available ? "true" : "false";
    card.innerHTML = `<strong>${item.label}</strong><span>${item.detail}</span>`;
    runtimeGrid.appendChild(card);
  });

  platformChips.innerHTML = "";
  data.featured_platforms.forEach((platform) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = platform;
    platformChips.appendChild(chip);
  });
}

function renderResult(data) {
  const { video, formats } = data;
  videoTitle.textContent = video.title;
  videoSubtitle.textContent = [video.uploader, formatDuration(video.duration)].filter(Boolean).join(" / ");

  if (video.thumbnail) {
    const thumbnailUrl = new URL("/api/thumbnail", window.location.origin);
    thumbnailUrl.searchParams.set("url", video.thumbnail);
    if (video.webpage_url) {
      thumbnailUrl.searchParams.set("referer", video.webpage_url);
    }
    thumbnail.onerror = () => {
      thumbnail.src = "/assets/logo.svg";
      thumbnail.classList.remove("is-cover");
      thumbnail.onerror = null;
    };
    thumbnail.src = thumbnailUrl.toString();
    thumbnail.classList.add("is-cover");
    thumbnail.classList.remove("hidden");
  } else {
    thumbnail.onerror = null;
    thumbnail.classList.add("hidden");
    thumbnail.classList.remove("is-cover");
  }

  renderVideoTags(video);
  renderFormatButtons(formats);

  resultNode.classList.remove("hidden");
}

function renderVideoTags(video) {
  videoTags.innerHTML = "";
  [video.platform, video.extractor, video.webpage_url ? "Download supported" : null].filter(Boolean).forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item;
    videoTags.appendChild(chip);
  });
}

function renderFormatButtons(formats) {
  const videos = formats.filter((format) => format.kind === "video").sort(compareFormats);
  const audios = formats.filter((format) => format.kind === "audio").sort(compareFormats);

  videoFormatGrid.innerHTML = "";
  audioFormatGrid.innerHTML = "";

  if (videos.length === 0) {
    videoFormatGrid.appendChild(createEmptyState("No downloadable video formats were returned for this URL."));
  } else {
    videos.forEach((format, index) => {
      videoFormatGrid.appendChild(createFormatCard(format, index === 0));
    });
  }

  if (audios.length === 0) {
    audioFormatGrid.appendChild(createEmptyState("No standalone audio formats were returned for this site in this request."));
  } else {
    audios.forEach((format) => {
      audioFormatGrid.appendChild(createFormatCard(format, false));
    });
  }
}

function compareFormats(left, right) {
  const leftScore = resolutionScore(left);
  const rightScore = resolutionScore(right);
  if (rightScore !== leftScore) {
    return rightScore - leftScore;
  }
  const leftAudio = audioScore(left);
  const rightAudio = audioScore(right);
  if (rightAudio !== leftAudio) {
    return rightAudio - leftAudio;
  }
  return (left.label || "").localeCompare(right.label || "", "zh-CN");
}

function resolutionScore(format) {
  const match = String(format.resolution || format.label || "").match(/(\d+)p/i);
  if (match) {
    return Number(match[1]);
  }
  return 0;
}

function audioScore(format) {
  const match = String(format.label || "").match(/(\d+)kbps/i);
  if (match) {
    return Number(match[1]);
  }
  return 0;
}

function createFormatCard(format, primary = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `format-card${primary ? " primary" : ""}`;
  button.disabled = format.downloadable === false;
  button.innerHTML = `
    <div class="format-top">
      <span class="format-title">${format.resolution || format.label}</span>
      <span class="chip">${format.downloadable === false ? "Restricted" : format.delivery_label || (format.delivery === "stream" ? "Streaming" : "Direct")}</span>
    </div>
    <div class="format-sub">${format.label}</div>
    <div class="format-meta">${[
      format.disabled_reason || format.note,
      format.ext ? format.ext.toUpperCase() : null,
      format.filesize ? humanSize(format.filesize) : null,
      format.protocol || null,
    ]
      .filter(Boolean)
      .join(" · ")}</div>
  `;
  if (format.downloadable !== false) {
    button.addEventListener("click", () => downloadFormat(format));
  }
  return button;
}

function createEmptyState(message) {
  const node = document.createElement("div");
  node.className = "empty-state";
  node.textContent = message;
  return node;
}

function formatDuration(seconds) {
  if (!seconds || Number.isNaN(seconds)) {
    return "";
  }
  const total = Number(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s]
    .filter((value, index) => value > 0 || index > 0)
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function humanSize(bytes) {
  if (!bytes) {
    return "";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatEta(seconds) {
  const total = Math.max(0, Number(seconds || 0));
  const mins = Math.floor(total / 60);
  const secs = Math.floor(total % 60);
  if (mins > 0) {
    return `${mins}m ${secs}s`;
  }
  return `${secs}s`;
}

async function resolveVideo(event) {
  event.preventDefault();
  resolveButton.disabled = true;
  resultNode.classList.add("hidden");
  resetProgress();
  setWarnings([]);
  setStatus("Resolving available formats...");

  const payload = {
    url: document.getElementById("url").value.trim(),
    cookie_source: cookieSource.value,
    browser: browserSelect.value || null,
    cookie_text: document.getElementById("cookie_text").value.trim() || null,
  };
  const authCache = readAuthCache();
  if (payload.cookie_source === "browser" && authCache?.browser === payload.browser) {
    payload.auth_token = authCache.auth_token;
  }

  lastPayload = payload;

  try {
    const response = await fetch("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(data.detail || "Resolve failed");
    }

    renderResult(data);
    lastPayload = { ...payload, auth_token: data.auth_token || null };
    if (payload.cookie_source === "browser" && data.auth_token) {
      writeAuthCache({ browser: payload.browser, auth_token: data.auth_token });
    }
    setWarnings(data.warnings || []);
    setStatus(`Found ${data.formats.length} downloadable formats.`);
  } catch (error) {
    if ((error.message || "").includes("Cached authentication has expired")) {
      clearAuthCache();
    }
    setStatus(error.message || "Resolve failed");
  } finally {
    resolveButton.disabled = false;
  }
}

async function downloadFormat(format) {
  if (!lastPayload || !format) {
    setStatus("Resolve the link first.");
    return;
  }

  if (activeDownloadPoll) {
    window.clearInterval(activeDownloadPoll);
    activeDownloadPoll = null;
  }

  setStatus("Creating download job...");
  progressPanel.classList.remove("hidden");
  updateProgress({ status: "queued", progress: 0, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null });

  const payload = {
    ...lastPayload,
    format_selector: format.selector,
    filename_hint: document.getElementById("video-title").textContent || "download",
    strategy: format.strategy || "default",
  };

  try {
    const response = await fetch("/api/download-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      let message = "Download failed";
      try {
        const error = await response.json();
        message = error.detail || message;
      } catch (_error) {
        message = await response.text() || message;
      }
      throw new Error(message);
    }

    const job = await response.json();
    updateProgress(job);
    setStatus("Download job started.");
    activeDownloadPoll = window.setInterval(() => pollDownloadJob(job.job_id), 1000);
  } catch (error) {
    if ((error.message || "").includes("Cached authentication has expired")) {
      clearAuthCache();
    }
    setStatus(error.message || "Download failed");
    updateProgress({ status: "failed", progress: 0, error: error.message || "Download failed" });
  }
}

async function pollDownloadJob(jobId) {
  try {
    const response = await fetch(`/api/download-jobs/${jobId}`);
    const job = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(job.detail || "Failed to fetch download progress");
    }
    updateProgress(job);

    if (job.status === "completed") {
      if (activeDownloadPoll) {
        window.clearInterval(activeDownloadPoll);
        activeDownloadPoll = null;
      }
      const anchor = document.createElement("a");
      anchor.href = `/api/download-jobs/${jobId}/file`;
      anchor.download = job.filename || "download.bin";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setStatus(`Download complete: ${job.filename || "file ready"}. The browser save flow has started.`);
    }

    if (job.status === "failed") {
      if (activeDownloadPoll) {
        window.clearInterval(activeDownloadPoll);
        activeDownloadPoll = null;
      }
      setStatus(job.error || "Download failed");
    }
  } catch (error) {
    if (activeDownloadPoll) {
      window.clearInterval(activeDownloadPoll);
      activeDownloadPoll = null;
    }
    setStatus(error.message || "Failed to fetch download progress");
  }
}

cookieSource.addEventListener("change", toggleAuthFields);
form.addEventListener("submit", resolveVideo);

toggleAuthFields();
resetProgress();
loadCapabilities().catch(() => {
  oauthTip.textContent = "Failed to load authentication capabilities. Public resources can still be resolved.";
});
loadEnvironment().catch(() => {
  supportSummary.textContent = "Failed to load environment details, but the core resolve flow is still available.";
});

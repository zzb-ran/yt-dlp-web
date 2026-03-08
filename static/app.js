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

function setStatus(message) {
  statusNode.textContent = message || "";
}

function resetProgress() {
  progressPanel.classList.add("hidden");
  progressPanel.classList.remove("is-complete");
  progressStatusText.textContent = "";
  progressBarFill.style.width = "0%";
  progressPercent.textContent = "0%";
  progressDetail.textContent = "等待下载";
}

function updateProgress(job) {
  progressPanel.classList.remove("hidden");
  progressPanel.classList.toggle("is-complete", job.status === "completed");
  const percent = Math.max(0, Math.min(100, job.progress || 0));
  progressBarFill.style.width = `${percent}%`;
  progressPercent.textContent = `${percent.toFixed(percent >= 100 ? 0 : 1)}%`;
  progressStatusText.textContent = ({
    queued: "任务已创建，等待开始",
    downloading: "正在下载中",
    completed: "下载成功，文件已准备完成",
    failed: "下载失败",
  })[job.status] || "处理中";

  const parts = [];
  if (job.status === "completed" && job.filename) {
    parts.push(`文件 ${job.filename}`);
  }
  if (job.downloaded_bytes) {
    parts.push(humanSize(job.downloaded_bytes));
  }
  if (job.total_bytes) {
    parts.push(`总计 ${humanSize(job.total_bytes)}`);
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
  progressDetail.textContent = parts.join(" · ") || "等待下载";
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
    thumbnail.src = video.thumbnail;
    thumbnail.classList.remove("hidden");
  } else {
    thumbnail.classList.add("hidden");
  }

  renderVideoTags(video);
  renderFormatButtons(formats);

  resultNode.classList.remove("hidden");
}

function renderVideoTags(video) {
  videoTags.innerHTML = "";
  [video.platform, video.extractor, video.webpage_url ? "支持下载" : null].filter(Boolean).forEach((item) => {
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
    videoFormatGrid.appendChild(createEmptyState("当前链接没有可直接下载的视频格式。"));
  } else {
    videos.forEach((format, index) => {
      videoFormatGrid.appendChild(createFormatCard(format, index === 0));
    });
  }

  if (audios.length === 0) {
    audioFormatGrid.appendChild(createEmptyState("当前站点这次没有返回可单独下载的音频格式。"));
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
      <span class="chip">${format.downloadable === false ? "受限" : format.delivery === "stream" ? "流媒体 m3u8" : "直链"}</span>
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
    return `${mins}分${secs}秒`;
  }
  return `${secs}秒`;
}

async function resolveVideo(event) {
  event.preventDefault();
  resolveButton.disabled = true;
  resultNode.classList.add("hidden");
  resetProgress();
  setWarnings([]);
  setStatus("正在解析可用格式...");

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
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "解析失败");
    }

    renderResult(data);
    lastPayload = { ...payload, auth_token: data.auth_token || null };
    if (payload.cookie_source === "browser" && data.auth_token) {
      writeAuthCache({ browser: payload.browser, auth_token: data.auth_token });
    }
    setWarnings(data.warnings || []);
    setStatus(`已找到 ${data.formats.length} 个可下载格式。`);
  } catch (error) {
    if ((error.message || "").includes("认证缓存已过期")) {
      clearAuthCache();
    }
    setStatus(error.message || "解析失败");
  } finally {
    resolveButton.disabled = false;
  }
}

async function downloadFormat(format) {
  if (!lastPayload || !format) {
    setStatus("请先解析链接。");
    return;
  }

  if (activeDownloadPoll) {
    window.clearInterval(activeDownloadPoll);
    activeDownloadPoll = null;
  }

  setStatus("正在创建下载任务...");
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
      let message = "下载失败";
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
    setStatus("下载任务已开始。");
    activeDownloadPoll = window.setInterval(() => pollDownloadJob(job.job_id), 1000);
  } catch (error) {
    if ((error.message || "").includes("认证缓存已过期")) {
      clearAuthCache();
    }
    setStatus(error.message || "下载失败");
    updateProgress({ status: "failed", progress: 0, error: error.message || "下载失败" });
  }
}

async function pollDownloadJob(jobId) {
  try {
    const response = await fetch(`/api/download-jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) {
      throw new Error(job.detail || "获取下载进度失败");
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
      setStatus(`下载成功：${job.filename || "文件已准备好"}，浏览器已开始保存。`);
    }

    if (job.status === "failed") {
      if (activeDownloadPoll) {
        window.clearInterval(activeDownloadPoll);
        activeDownloadPoll = null;
      }
      setStatus(job.error || "下载失败");
    }
  } catch (error) {
    if (activeDownloadPoll) {
      window.clearInterval(activeDownloadPoll);
      activeDownloadPoll = null;
    }
    setStatus(error.message || "获取下载进度失败");
  }
}

cookieSource.addEventListener("change", toggleAuthFields);
form.addEventListener("submit", resolveVideo);

toggleAuthFields();
resetProgress();
loadCapabilities().catch(() => {
  oauthTip.textContent = "认证能力加载失败，默认仍可直接解析公开资源。";
});
loadEnvironment().catch(() => {
  supportSummary.textContent = "环境信息加载失败，但基础解析功能仍可使用。";
});

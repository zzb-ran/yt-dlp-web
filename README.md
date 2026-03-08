# yt-dlp Fetch

一个本地优先的 yt-dlp 下载站，前端样式按你给的截图方向实现，支持：

- 粘贴 URL 后解析可下载格式
- 不同清晰度 / 音频格式选择
- 本机浏览器 cookies 读取
- 部署场景下粘贴 `cookies.txt`
- 本地运行优先，同时保留前后端分离部署空间

## 结构

- `app/`: FastAPI 后端与 yt-dlp 集成
- `static/`: 单页前端
- `tests/`: 基础测试

## 一键启动

### 统一入口

如果机器上已经有 `Python 3.12+`，可以直接用一个文件启动：

```bash
python start.py
```

它会自动识别当前平台是 Windows / macOS / Linux，并执行对应的环境检测与安装流程。

### macOS / Linux

```bash
chmod +x start.sh
./start.sh
```

### Windows PowerShell

```powershell
.\start.ps1
```

### Windows CMD

```cmd
start.cmd
```

脚本会自动处理：

- 检测并安装 `Python 3.12`
- 检测并安装 `Node.js >= 20`
- 检测并安装 `ffmpeg`
- 检测并安装 `Deno`
- 检测并安装 `Git`
- 创建项目虚拟环境 `.venv`
- 安装 Python 依赖
- 安装并编译 `bgutil` PO Token Provider
- 启动 FastAPI 服务

运行前提：

- 设备能联网
- Windows 首次运行建议用“管理员 PowerShell”
- Linux 首次运行需要当前用户能使用 `sudo`
- Linux 当前脚本支持 `apt / dnf / yum / pacman`
- `start.py` 需要当前机器已具备 `Python 3.12+`

启动后打开：

```text
http://127.0.0.1:8000
```

## 手动运行

如果你不想用一键脚本，也可以手动执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd tools/bgutil-ytdlp-pot-provider/server
npm ci
npx tsc
cd ../../..
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 额外依赖

- `ffmpeg`: 合并视频和音频必需
- `deno`: YouTube JS challenge 求解必需
- `node`: `bgutil` PO Token Provider server 依赖

如果你是在一台全新机器上，优先用上面的启动脚本，不建议先手动装旧版本 Python。

## 认证说明

截至 2026-03-08，`yt-dlp` 官方 Wiki 已注明 YouTube OAuth 因站点限制不可用，因此本项目没有实现一个不可工作的 OAuth 登录入口。

可用方案：

- 本地运行：前端选择“读取本机浏览器 cookies”
- 部署运行：前端选择“粘贴 / 上传 cookies.txt”

## 部署建议

### 方案 A：本地 / VPS 一体运行

最稳妥。直接运行 FastAPI 即可。

### 方案 B：前端放 CF Pages，后端单独部署

适合免费组合：

- 前端：Cloudflare Pages
- 后端：Render / Railway / Fly.io / 自己的服务器

注意：Cloudflare Workers 不能直接跑 `yt-dlp` 这种依赖二进制和子进程的下载逻辑。

### 方案 C：Cloudflare Containers

Cloudflare Containers 在 2026-03-08 仍是 Beta，且要求 Workers Paid。可以作为后续增强，但不适合作为第一落点。

## API

### `GET /api/auth/capabilities`

返回认证方式能力与浏览器列表。

### `POST /api/resolve`

请求示例：

```json
{
  "url": "https://www.youtube.com/watch?v=xxxx",
  "cookie_source": "browser",
  "browser": "chrome",
  "cookie_text": null
}
```

### `POST /api/download`

请求示例：

```json
{
  "url": "https://www.youtube.com/watch?v=xxxx",
  "cookie_source": "none",
  "browser": null,
  "cookie_text": null,
  "format_selector": "137+251",
  "filename_hint": "example"
}
```

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RootDir ".venv"
$ProviderServerDir = Join-Path $RootDir "tools\bgutil-ytdlp-pot-provider\server"
$HostAddress = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$Port = if ($env:PORT) { $env:PORT } else { "8000" }

function Write-Log($Message) {
    Write-Host "[ytfetch] $Message"
}

function Refresh-Path {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Test-PythonVersion($Command, $Arguments) {
    try {
        $output = & $Command @Arguments -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null
        if (-not $output) {
            return $false
        }
        $parts = $output.Trim().Split(".")
        return ([int]$parts[0] -gt 3) -or (([int]$parts[0] -eq 3) -and ([int]$parts[1] -ge 12))
    } catch {
        return $false
    }
}

function Get-PythonLauncher {
    if (Get-Command python3.12 -ErrorAction SilentlyContinue) {
        if (Test-PythonVersion "python3.12" @()) {
            return @{ Command = "python3.12"; Arguments = @() }
        }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        if (Test-PythonVersion "py" @("-3.12")) {
            return @{ Command = "py"; Arguments = @("-3.12") }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        if (Test-PythonVersion "python" @()) {
            return @{ Command = "python"; Arguments = @() }
        }
    }
    throw "未检测到可用的 Python 3.12+"
}

function Ensure-Command($Command, $WingetId, $DisplayName) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) {
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "未检测到 winget，无法自动安装 $DisplayName"
    }
    Write-Log "安装 $DisplayName"
    winget install --accept-package-agreements --accept-source-agreements -e --id $WingetId
    Refresh-Path
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "$DisplayName 安装失败，请手动检查 winget 输出"
    }
}

function Ensure-NodeVersion {
    $nodeVersion = (node --version).TrimStart("v")
    $major = [int]($nodeVersion.Split(".")[0])
    if ($major -lt 20) {
        throw "当前 Node.js 版本过低（$nodeVersion），需要 >= 20"
    }
}

function Ensure-SystemDeps {
    Ensure-Command "git" "Git.Git" "Git"
    if (-not (Get-Command python3.12 -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
        Ensure-Command "python" "Python.Python.3.12" "Python 3.12"
    }
    Ensure-Command "node" "OpenJS.NodeJS.LTS" "Node.js LTS"
    Ensure-Command "npm" "OpenJS.NodeJS.LTS" "Node.js LTS"
    Ensure-Command "ffmpeg" "Gyan.FFmpeg" "FFmpeg"
    Ensure-Command "deno" "DenoLand.Deno" "Deno"
    Ensure-NodeVersion
    if (-not (Test-Path $ProviderServerDir)) {
        throw "缺少 provider 目录: $ProviderServerDir"
    }
    [void](Get-PythonLauncher)
}

function Ensure-Venv {
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $python = Get-PythonLauncher
        Write-Log "创建 Python 虚拟环境（$($python.Command) $($python.Arguments -join ' ')）"
        & $python.Command @($python.Arguments + @("-m", "venv", $VenvDir))
    }
}

function Install-PythonDeps {
    Write-Log "安装 Python 依赖"
    & (Join-Path $VenvDir "Scripts\python.exe") -m pip install --upgrade pip
    & (Join-Path $VenvDir "Scripts\pip.exe") install -r (Join-Path $RootDir "requirements.txt")
}

function Install-ProviderServer {
    Write-Log "安装并编译 bgutil provider server"
    Push-Location $ProviderServerDir
    try {
        npm ci
        npx tsc
    } finally {
        Pop-Location
    }
}

function Start-App {
    Write-Log "启动服务 http://$HostAddress`:$Port"
    & (Join-Path $VenvDir "Scripts\uvicorn.exe") app.main:app --host $HostAddress --port $Port
}

Set-Location $RootDir
Ensure-SystemDeps
Ensure-Venv
Install-PythonDeps
Install-ProviderServer
Start-App

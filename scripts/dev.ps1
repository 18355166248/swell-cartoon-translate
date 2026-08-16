<#
.SYNOPSIS
  同时启动后端和前端，并打开浏览器。

.DESCRIPTION
  所有路径都相对脚本自身解析，所以在任何目录下执行都一样 —— 不像
  `cd backend && ...` 那样，在已经位于 backend 里时会失败。

  后端在后台窗口运行，前端占用当前终端；Ctrl+C 结束前端后会一并关掉后端。

.PARAMETER BackendPort
  后端端口，默认 8000。改这个也要同步改 frontend/vite.config.ts 里的代理目标。

.PARAMETER NoBrowser
  不自动打开浏览器。
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Test-Port($port) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $port)
        $c.Close()
        return $true
    } catch { return $false }
}

if (-not (Test-Path "$root\frontend\node_modules")) {
    Write-Host "首次运行，安装前端依赖..." -ForegroundColor Yellow
    npm --prefix "$root\frontend" install
}

# ------------------------------------------------------------------ 后端 ---
$backend = $null
if (Test-Port $BackendPort) {
    Write-Host "后端已在 $BackendPort 端口运行，直接复用" -ForegroundColor Yellow
} else {
    Write-Host "启动后端 http://127.0.0.1:$BackendPort ..." -ForegroundColor Cyan
    $backend = Start-Process -PassThru -WindowStyle Minimized `
        -FilePath "python" `
        -ArgumentList @(
            "-m", "uvicorn", "ctt.server:app",
            "--port", "$BackendPort",
            "--app-dir", "$root\backend"
        )

    # 等后端真正可连，而不是固定 sleep：模型加载耗时因机器而异。
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Port $BackendPort)) {
        if ((Get-Date) -gt $deadline) {
            Write-Host "后端 60 秒内未就绪，检查上面那个窗口的报错" -ForegroundColor Red
            if ($backend -and -not $backend.HasExited) { $backend.Kill() }
            exit 1
        }
        if ($backend.HasExited) {
            Write-Host "后端进程已退出，检查报错" -ForegroundColor Red
            exit 1
        }
        Start-Sleep -Milliseconds 300
    }
    Write-Host "后端就绪" -ForegroundColor Green
}

if (-not $NoBrowser) {
    Start-Process "http://localhost:$FrontendPort"
}

# ------------------------------------------------------------------ 前端 ---
Write-Host "启动前端 http://localhost:$FrontendPort  (Ctrl+C 结束)" -ForegroundColor Cyan
try {
    npm --prefix "$root\frontend" run dev
} finally {
    if ($backend -and -not $backend.HasExited) {
        Write-Host "`n关闭后端..." -ForegroundColor Yellow
        $backend.Kill()
    }
}

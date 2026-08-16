<#
.SYNOPSIS
  一键初始化：装依赖、下模型、自检。换电脑后跑这一个脚本就能用。

.DESCRIPTION
  幂等设计——重复跑是安全的，已经装好的会跳过。
  所以中途断网、下载失败，直接重跑即可，不用先清理。

  模型权重（约 4.7GB）不在仓库里，由本脚本下载到 backend/models/。

.PARAMETER SkipModels
  只装 Python 依赖，不下模型。适合先把环境搭好、晚点再下大文件。

.PARAMETER Cpu
  强制 CPU-only（默认）。GPU 相关的包体积大得多，而本项目的设计就是
  让翻译走 CPU、把显存留给别的程序。

.EXAMPLE
  .\scripts\setup.ps1
  .\scripts\setup.ps1 -SkipModels
#>
[CmdletBinding()]
param(
    [switch]$SkipModels,
    [switch]$Cpu = $true
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$failed = @()

function Step($name) { Write-Host "`n=== $name ===" -ForegroundColor Cyan }
function Ok($msg)     { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Warn($msg)   { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Fail($msg)   { Write-Host "  [FAIL] $msg" -ForegroundColor Red; $script:failed += $msg }

function Test-Module($name) {
    python -c "import $name" 2>$null
    return $LASTEXITCODE -eq 0
}

# ------------------------------------------------------------------ Python ---
Step "检查 Python"
try {
    $version = (python --version 2>&1) -replace 'Python ', ''
    $parts = $version.Split('.')
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
        Fail "需要 Python 3.11+（tomllib 是 3.11 才进标准库的），当前 $version"
        exit 1
    }
    Ok "Python $version"
} catch {
    Fail "找不到 python，请先安装 Python 3.11+ 并加入 PATH"
    exit 1
}

# -------------------------------------------------------------------- 依赖 ---
Step "安装 Python 依赖"

# 分组安装：某一组失败不影响其它组，且能准确报出是哪一组坏了。
$groups = @(
    @{ Name = "核心";   Args = @("numpy", "opencv-python-headless", "pillow", "pydantic"); Probe = "cv2" }
    @{ Name = "检测";   Args = @("onnxruntime", "huggingface-hub");                        Probe = "onnxruntime" }
    @{ Name = "OCR";    Args = @("paddlepaddle", "paddleocr");                             Probe = "paddleocr" }
    @{ Name = "翻译";   Args = @("llama-cpp-python", "--extra-index-url",
                                 "https://abetlen.github.io/llama-cpp-python/whl/cpu");    Probe = "llama_cpp" }
    @{ Name = "服务";   Args = @("fastapi", "uvicorn[standard]", "python-multipart");      Probe = "fastapi" }
    @{ Name = "测试";   Args = @("pytest");                                                Probe = "pytest" }
)

foreach ($g in $groups) {
    if (Test-Module $g.Probe) {
        Ok "$($g.Name) 已安装，跳过"
        continue
    }
    Write-Host "  安装 $($g.Name)..."
    python -m pip install --quiet @($g.Args) 2>&1 | Out-Null
    if (Test-Module $g.Probe) {
        Ok "$($g.Name)"
    } else {
        Fail "$($g.Name) 安装失败：python -m pip install $($g.Args -join ' ')"
    }
}

# -------------------------------------------------------------------- 字体 ---
# 这里以及下面的模型下载都调 scripts/bootstrap.py。把 Python 塞进 PowerShell
# here-string 会踩解析规则（`from` 会被当成 PowerShell 关键字），单独成文件
# 既稳当又能自己跑来调试。
Step "检查中文字体"
python "$PSScriptRoot\bootstrap.py" --check-fonts
if ($LASTEXITCODE -eq 0) {
    Ok "字体就绪"
} else {
    Warn "没找到中文字体，排版会渲染成方块"
}

# -------------------------------------------------------------------- 模型 ---
if ($SkipModels) {
    Step "模型下载"
    Warn "已跳过（-SkipModels）。首次翻译时会自动下载。"
} else {
    Step "下载模型（约 4.7GB，只需一次）"
    python "$PSScriptRoot\bootstrap.py" --models
    if ($LASTEXITCODE -eq 0) { Ok "模型就绪" } else { Fail "模型下载失败，重跑本脚本即可续传" }
}

# -------------------------------------------------------------------- 自检 ---
Step "自检"
Push-Location "$root\backend"
python -m pytest tests/ -q 2>&1 | Select-Object -Last 1
if ($LASTEXITCODE -eq 0) { Ok "测试通过" } else { Fail "测试未通过" }

python -m ctt.cli config 2>&1 | Select-Object -First 2
Pop-Location

# -------------------------------------------------------------------- 收尾 ---
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "初始化完成。" -ForegroundColor Green
    Write-Host @"

下一步：
  1. 把漫画图片放进 assets\ （或任意目录）
  2. cd backend
  3. python -m ctt.cli translate ..\assets -o ..\out

配置改 ctt.toml，查看当前生效配置：python -m ctt.cli config
需要密钥的后端用环境变量：DEEPL_API_KEY / CTT_LLM_KEY
"@
} else {
    Write-Host "有 $($failed.Count) 项未完成：" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "`n修好后重跑本脚本，已完成的部分会自动跳过。" -ForegroundColor Yellow
    exit 1
}

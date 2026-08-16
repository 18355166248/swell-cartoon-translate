<#
.SYNOPSIS
  Remove everything this project installed: pip packages and downloaded model weights.

.DESCRIPTION
  The package list is not guessed -- it is the measured difference between
  `pip list` before and after each install step, recorded in this directory.

  IMPORTANT: this deliberately does NOT wipe %USERPROFILE%\.cache\huggingface.
  That cache is shared with every other tool that uses the Hugging Face Hub.
  On the machine this was developed against it already held a 1.6GB
  stabilityai/TripoSR checkpoint belonging to an unrelated project, which a
  blanket "clear the model cache" would have destroyed. Only the specific
  subdirectories this project created are removed.

.PARAMETER WhatIf
  Show what would be removed without removing it. Run this first.

.EXAMPLE
  .\scripts\uninstall.ps1 -WhatIf
  .\scripts\uninstall.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$KeepPackages,
    [switch]$KeepModels,

    # Remove only the local-LLM translation tier (llama-cpp-python + the GGUF),
    # leaving detection, OCR and the rest of the pipeline working. This is the
    # "try it, back it out if it disappoints" path -- it reclaims the ~4.4GB
    # model without undoing the parts that are already verified good.
    [switch]$LlmOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Get-DirSizeMB($path) {
    if (-not (Test-Path $path)) { return 0 }
    $sum = (Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum
    return [math]::Round($sum / 1MB)
}

# ---------------------------------------------------------------- packages ---
if (-not $KeepPackages) {
    # Each file lists the packages one install step added, newest first so
    # dependents are removed before their dependencies.
    $manifests = if ($LlmOnly) {
        @("$PSScriptRoot\llamacpp-added-packages.txt")
    } else {
        @(
            "$PSScriptRoot\llamacpp-added-packages.txt",
            "$PSScriptRoot\nllb-added-packages.txt"
        )
    }

    $packages = foreach ($m in $manifests) {
        if (Test-Path $m) { Get-Content $m | ForEach-Object { ($_ -split '==')[0] } }
    }
    $declared = $packages | Where-Object { $_ } | Select-Object -Unique

    if (-not $declared) {
        Write-Warning "No package manifest found in $PSScriptRoot; skipping pip uninstall."
    } else {
        # Drop anything already gone or still needed by another package.
        # See scripts/plan_uninstall.py for why this is conservative.
        $packages = @(& python "$PSScriptRoot\plan_uninstall.py" @declared)

        if ($packages.Count -gt 0) {
            Write-Host "`n=== pip packages to remove ($($packages.Count)) ===" -ForegroundColor Cyan
            $packages | ForEach-Object { Write-Host "  $_" }
            if ($PSCmdlet.ShouldProcess("$($packages.Count) pip package(s)", "uninstall")) {
                python -m pip uninstall -y @packages
            }
        } else {
            Write-Host "`n=== pip packages: nothing to remove ===" -ForegroundColor Cyan
            Write-Host "  All $($declared.Count) package(s) in the manifest are already gone or still in use."
        }
    }

    Write-Host "`n  Note: paddleocr/paddlepaddle are NOT in the manifest -- they were" -ForegroundColor Yellow
    Write-Host "  installed before the snapshot was taken. Remove them by hand if wanted:" -ForegroundColor Yellow
    Write-Host "    python -m pip uninstall -y paddleocr paddlepaddle" -ForegroundColor Yellow
}

# ------------------------------------------------------------------ models ---
if ($LlmOnly) {
    # Just the GGUF. Everything else stays put.
    $gguf = "$root\backend\models\gguf"
    Write-Host "`n=== local-LLM model files ===" -ForegroundColor Cyan
    if (Test-Path $gguf) {
        Write-Host ("  {0,8:N0} MB  {1}" -f (Get-DirSizeMB $gguf), $gguf)
        if ($PSCmdlet.ShouldProcess($gguf, "remove directory")) {
            Remove-Item $gguf -Recurse -Force
        }
    } else {
        Write-Host "  nothing to remove"
    }
    Write-Host "`nLLM tier removed. Detection, OCR and typesetting are untouched." -ForegroundColor Green
    Write-Host "The pipeline falls back to the next backend in --translators." -ForegroundColor Green
    return
}

if (-not $KeepModels) {
    # Project-local first: everything downloaded after ctt/paths.py landed goes
    # here, so this one directory covers the detector and the GGUF.
    $targets = @(
        @{ Path = "$root\backend\models";                          Why = "project model cache (detector, GGUF)" }
        @{ Path = "$env:USERPROFILE\.paddlex";                     Why = "PaddleOCR recognition weights" }
    )

    # These are the only entries this project created inside the *shared* HF
    # cache, from runs that predate ctt/paths.py. Named individually on purpose.
    $hub = "$env:USERPROFILE\.cache\huggingface\hub"
    $ours = @(
        "models--ogkalu--comic-text-and-bubble-detector",
        "models--PaddlePaddle--korean_PP-OCRv5_mobile_rec",
        "models--PaddlePaddle--PP-LCNet_x1_0_doc_ori",
        "models--PaddlePaddle--PP-LCNet_x1_0_textline_ori",
        "models--PaddlePaddle--PP-OCRv5_server_det",
        "models--PaddlePaddle--UVDoc"
    )
    foreach ($name in $ours) {
        $targets += @{ Path = "$hub\$name"; Why = "shared HF cache entry created by this project" }
    }

    Write-Host "`n=== model directories to remove ===" -ForegroundColor Cyan
    $total = 0
    foreach ($t in $targets) {
        if (Test-Path $t.Path) {
            $mb = Get-DirSizeMB $t.Path
            $total += $mb
            Write-Host ("  {0,8:N0} MB  {1}" -f $mb, $t.Path)
            Write-Host ("            {0}" -f $t.Why) -ForegroundColor DarkGray
            if ($PSCmdlet.ShouldProcess($t.Path, "remove directory")) {
                Remove-Item $t.Path -Recurse -Force
            }
        }
    }
    Write-Host ("  {0,8:N0} MB  TOTAL" -f $total) -ForegroundColor Cyan

    if (Test-Path $hub) {
        # Exclude our own targets by name rather than by "still on disk":
        # under -WhatIf nothing was actually deleted, so a existence check
        # would list every entry above as if it belonged to someone else.
        $left = Get-ChildItem $hub -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -notlike '.*' -and $_.Name -notin $ours }
        if ($left) {
            Write-Host "`n  Left alone in the shared HF cache (belongs to other tools):" -ForegroundColor Yellow
            $left | ForEach-Object {
                Write-Host ("    {0,8:N0} MB  {1}" -f (Get-DirSizeMB $_.FullName), $_.Name) -ForegroundColor Yellow
            }
        }
    }
}

Write-Host "`nDone. The repo itself (source, assets, out/) is untouched." -ForegroundColor Green

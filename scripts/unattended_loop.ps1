# unattended_loop.ps1 — PTME integration loop (Windows companion to unattended_loop.sh).
#
# Routes the queue, then drives the per-model supervisors so each task runs under
# its PTME-resolved model + effort. For local Windows workstation runs / Scheduled
# Tasks. NO SECRETS — reads only env-var names; never echoes credentials.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\unattended_loop.ps1
#   $env:MODELS = "codex"; .\scripts\unattended_loop.ps1
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot

# agy requires TERM=xterm even on Windows when launched without a console.
if (-not $env:TERM) { $env:TERM = "xterm" }

$Models = if ($env:MODELS) { $env:MODELS -split '\s+' } else { @("codex", "antigravity", "claude-code") }

Write-Host "[unattended_loop] routing queue..."
python scripts/task_router.py

foreach ($model in $Models) {
    Write-Host "[unattended_loop] supervising model: $model"
    python scripts/model_supervisor.py run --model $model
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[unattended_loop] supervisor for $model exited non-zero (continuing)"
    }
}

Write-Host "[unattended_loop] pass complete: $((Get-Date).ToUniversalTime().ToString('s'))Z"

<#
.SYNOPSIS
    Invoke agy (Antigravity CLI) non-interactively, with a
    backend HEALTH PREFLIGHT that fails loudly instead of masking a dead backend.

.DESCRIPTION
    IMPORTANT (corrected 2026-06-26): agy `--print` PRINTS its response to STDOUT.
    It does NOT silently "write files instead of stdout" - that earlier claim was a
    misconception that hid real failures. File outputs happen ONLY when the task
    prompt explicitly tells agy to write files AND the Antigravity backend is healthy.

    agy depends on an authenticated CLI backend. If that session is expired or
    unavailable, agy returns EMPTY output (exit 0) or HANGS. The old
    wrapper reported that as "No new files detected" - a silent no-op that looked like
    success and wasted dispatches.

    This wrapper now:
      1. Runs a fast HEALTH PROBE first; if agy does not return the AGY_OK token it
         refuses to dispatch (exit 2) and requests re-authentication.
      2. Adds --dangerously-skip-permissions so agy can use tools/write files unattended.
      3. Treats exit-0 + empty-output + zero-new-files as a FAILURE (exit 3).

    Exit codes: 0 success; 2 backend unavailable (re-auth agy); 3 ran but produced
    nothing (silent no-op); 124 hard timeout; -1 invocation threw; else agy's own code.

.PARAMETER Prompt           The task prompt to send to agy.
.PARAMETER WaitSeconds      Settle delay AFTER agy exits, for async file writes. Default 30.
.PARAMETER TimeoutSeconds   HARD wall-clock limit for the real agy run. Default 300.
.PARAMETER AgePath          Full path to agy.exe.
.PARAMETER WorkspaceDir     Antigravity workspace to run in and scan for new files.
.PARAMETER SkipPreflight    Skip the health probe (not recommended).

.EXAMPLE
    .\invoke_agy.ps1 -Prompt "Summarize the latest AI news and save to a file"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Prompt,
    [int]$WaitSeconds = 30,
    [int]$TimeoutSeconds = 0,
    [string]$AgePath = "",
    [string]$WorkspaceDir = "",
    [switch]$SkipPreflight
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "aoa_config.ps1")
if ($TimeoutSeconds -eq 0) { $TimeoutSeconds = [int](Get-AoaConfigValue "timeouts.agy_invoke_seconds") }
if (-not $AgePath) { $AgePath = Get-AoaConfigValue "cli.agy" }
if (-not $WorkspaceDir) { $WorkspaceDir = Get-AoaConfigValue "paths.workdir" }

if (-not (Get-Command $AgePath -ErrorAction SilentlyContinue)) { Write-Error "agy executable not found: $AgePath"; exit 1 }
if (-not (Test-Path $WorkspaceDir)) { Write-Error "Workspace directory not found: $WorkspaceDir"; exit 1 }

# Required: without TERM=xterm agy hangs indefinitely.
$env:TERM = "xterm"

# --- Single raw agy invocation with a hard wall-clock kill. Returns {ExitCode, Output}. ---
function Invoke-AgyRaw {
    param([string]$TaskPrompt, [int]$HardTimeoutSec, [int]$PrintTimeoutSec)

    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    $stdinFile  = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $stdinFile -Value $TaskPrompt -Encoding UTF8

    $code = -1
    try {
        $proc = Start-Process -FilePath $AgePath `
            -ArgumentList @("--dangerously-skip-permissions", "--print", $TaskPrompt, "--print-timeout", "${PrintTimeoutSec}s") `
            -WorkingDirectory $WorkspaceDir `
            -RedirectStandardInput $stdinFile `
            -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile `
            -NoNewWindow -PassThru

        $finished = $proc.WaitForExit($HardTimeoutSec * 1000)
        if (-not $finished) {
            & taskkill /PID $proc.Id /T /F 2>$null | Out-Null
            $null = $proc.WaitForExit(5000)   # confirm tree died + redirected I/O flushed before harvest
            $code = 124
        }
        else { $code = $proc.ExitCode }
    }
    catch { $code = -1 }

    $out = (Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue) + `
           (Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue)
    Remove-Item $stdoutFile, $stderrFile, $stdinFile -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{ ExitCode = $code; Output = $out }
}

Write-Host "[invoke_agy] TERM=xterm | Workspace: $WorkspaceDir" -ForegroundColor Cyan

# --- HEALTH PREFLIGHT: fail loudly if the agy backend is dead. ---
if (-not $SkipPreflight) {
    Write-Host "[invoke_agy] preflight: probing agy backend..." -ForegroundColor Cyan
    $probe = Invoke-AgyRaw -TaskPrompt "Reply with exactly this token and nothing else: AGY_OK" -HardTimeoutSec 45 -PrintTimeoutSec 30
    if ($probe.Output -notmatch "AGY_OK") {
        Write-Host ""
        [Console]::Error.WriteLine(@"
AGY BACKEND UNAVAILABLE - health probe returned no AGY_OK (empty or hang).
Most likely the configured agy CLI auth/session expired or is unavailable.
ACTION: run
  `$env:TERM='xterm'; & "$AgePath"
once interactively and complete login. Then retry.
Refusing to dispatch into a dead backend (no wasted run).
"@)
        exit 2
    }
    Write-Host "[invoke_agy] preflight OK (AGY_OK received)" -ForegroundColor Green
}

# --- pre-run snapshot (path -> LastWriteTime ticks) for new-file detection ---
$snapshot = @{}
Get-ChildItem -Path $WorkspaceDir -Recurse -File | ForEach-Object { $snapshot[$_.FullName] = $_.LastWriteTime.Ticks }

# --- the real task run ---
Write-Host "[invoke_agy] dispatching task (hard timeout ${TimeoutSeconds}s)..." -ForegroundColor Cyan
$result = Invoke-AgyRaw -TaskPrompt $Prompt -HardTimeoutSec $TimeoutSeconds -PrintTimeoutSec $TimeoutSeconds
$exitCode = $result.ExitCode

$color = if ($exitCode -eq 0) { "Green" } else { "Yellow" }
Write-Host "[invoke_agy] agy exited with code: $exitCode" -ForegroundColor $color
if ($result.Output -and $result.Output.Trim()) {
    Write-Host "[invoke_agy] agy response (stdout):" -ForegroundColor Gray
    Write-Host $result.Output -ForegroundColor Gray
}

Write-Host "[invoke_agy] settling ${WaitSeconds}s for async file writes..." -ForegroundColor Cyan
Start-Sleep -Seconds $WaitSeconds

$newFiles = Get-ChildItem -Path $WorkspaceDir -Recurse -File | Where-Object {
    (-not $snapshot.ContainsKey($_.FullName)) -or ($_.LastWriteTime.Ticks -gt $snapshot[$_.FullName])
} | Sort-Object LastWriteTime -Descending

Write-Host ""
if (@($newFiles).Count -eq 0) {
    Write-Host "[invoke_agy] No new/modified files in workspace." -ForegroundColor Yellow
}
else {
    Write-Host "[invoke_agy] New/modified files ($(@($newFiles).Count)):" -ForegroundColor Green
    foreach ($f in $newFiles) { Write-Host "  $($f.FullName)  [$($f.LastWriteTime.ToString('HH:mm:ss'))]" -ForegroundColor Green }
}

# --- FAIL-LOUD: exit-0 but produced nothing = silent no-op, NOT success ---
if ($exitCode -eq 0 -and [string]::IsNullOrWhiteSpace($result.Output) -and @($newFiles).Count -eq 0) {
    Write-Host ""
    [Console]::Error.WriteLine("AGY RUN PRODUCED NO OUTPUT (silent no-op) - treating as FAILURE. The backend likely dropped mid-run; re-authenticate agy and retry.")
    exit 3
}

$newFiles | Select-Object -ExpandProperty FullName | Write-Output
exit $exitCode

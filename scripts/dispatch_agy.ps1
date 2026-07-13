<#
.SYNOPSIS
    Dispatch a task to agy headlessly via the ConPTY wrapper (scripts/agy_pty.py).

.DESCRIPTION
    agy --print silently drops stdout under a non-TTY (known bug: antigravity-cli #76).
    agy_pty.py allocates a real pseudo-terminal (pywinpty/ConPTY) so agy runs headless and
    its output is captured. This makes agy a fully autonomous 3rd pipeline agent - NO interactive
    terminal needed. Real tool/file work succeeds; tell agy absolute paths to write
    to repo files (agy otherwise defaults writes to ~/.gemini/antigravity-cli/scratch/).

    Health preflight probes AGY_OK; fails loud (exit 2 = needs re-auth, exit 3 = empty output).
    Writes telemetry start/stop so the dashboard shows agy running.

.EXAMPLE
    .\dispatch_agy.ps1 -PromptFile scratchpad\refactor_spec.md -TaskId REFACTOR-1 -Role coder -Desc "slim GEMINI.md"
#>
param(
    [string]$Prompt,
    [string]$PromptFile,
    [Parameter(Mandatory = $true)][string]$TaskId,
    [string]$Role = "content",
    [string]$Desc = "agy task",
    [string]$WorkDir = "",
    [int]$TimeoutSeconds = 0,
    [string]$OutFile,
    [string]$Model = "",
    [switch]$SkipHealth
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "aoa_config.ps1")
if (-not $WorkDir) { $WorkDir = Get-AoaConfigValue "paths.workdir" }
if ($TimeoutSeconds -eq 0) { $TimeoutSeconds = [int](Get-AoaConfigValue "timeouts.agy_dispatch_seconds") }
$HealthTimeoutSeconds = [int](Get-AoaConfigValue "timeouts.health_probe_seconds")
$PythonCmd = Get-AoaConfigValue "cli.python"

$PtyPy       = Join-Path $PSScriptRoot "agy_pty.py"
$ResultPy    = Join-Path $PSScriptRoot "agy_result.py"
$TelemetryPy = Join-Path $PSScriptRoot "agent_telemetry.py"
$agent       = "agy-$Role"

if (-not (Test-Path $PtyPy)) { [Console]::Error.WriteLine("MISSING $PtyPy"); exit 3 }

if ($PromptFile) {
    if (-not (Test-Path $PromptFile)) { [Console]::Error.WriteLine("PromptFile not found: $PromptFile"); exit 1 }
    $body = Get-Content (Resolve-Path $PromptFile).Path -Raw
}
elseif ($Prompt) { $body = $Prompt }
else { [Console]::Error.WriteLine("Provide -Prompt or -PromptFile"); exit 1 }

# ---- token-efficiency preamble (same discipline as codex) ----
$preamble = @"
[EFFICIENCY] Be concise and token-frugal so usage lasts the week. No filler, no restating the task.
Do the work, write files to the ABSOLUTE paths given, then reply with a short status line only.

"@
$body = $preamble + $body

# ---- health preflight ----
if (-not $SkipHealth) {
    $probe = & $PythonCmd $PtyPy --prompt "Reply with exactly: AGY_OK" --timeout $HealthTimeoutSeconds 2>$null
    if ($LASTEXITCODE -ne 0 -or "$probe" -notmatch "AGY_OK") {
        [Console]::Error.WriteLine("AGY HEALTH FAIL (exit=$LASTEXITCODE, out='$probe'). agy may need re-auth: run 'agy' interactively and log in, or check scripts/agy_pty.py / pywinpty install.")
        exit 2
    }
}

# ---- telemetry start ----
$telemetryModel = if ($Model) { $Model } else { "gemini" }
try { & $PythonCmd $TelemetryPy start --agent $agent --task $TaskId --desc $Desc --model $telemetryModel --role $Role --effort "medium" --reason "agy conpty dispatch" | Out-Null } catch {}

# ---- run via ConPTY ----
$tmp = Join-Path $env:TEMP ("agyprompt_" + $TaskId + ".txt")
Set-Content $tmp $body -Encoding UTF8
$out = ""
try {
    if ($Model) {
        $out = & $PythonCmd $PtyPy --prompt-file $tmp --workdir $WorkDir --timeout $TimeoutSeconds --model $Model 2>&1 | Out-String
    } else {
        $out = & $PythonCmd $PtyPy --prompt-file $tmp --workdir $WorkDir --timeout $TimeoutSeconds 2>&1 | Out-String
    }
    $ptyExit = $LASTEXITCODE
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

$timedOut = ($ptyExit -eq 124) -or ($out -match "(?m)^AGY_PTY_ERROR:\s*timeout\s*$")
$ptyFailed = ($ptyExit -ne 0) -or ($out -match "(?m)^AGY_PTY_ERROR:")
$status = ($out | & $PythonCmd $ResultPy --exit-code $ptyExit | Out-String).Trim()
try { & $PythonCmd $TelemetryPy stop --agent $agent --status $status | Out-Null } catch {}

# ---- learning loop: log the outcome so the agent-strength table learns from every dispatch ----
try {
    $succ = if ($status -eq "done") { 1 } else { 0 }
    $learningLoop = Join-Path $PSScriptRoot "learning_loop.py"
    if (Test-Path $learningLoop) { & $PythonCmd $learningLoop record --engine agy --role $Role --success $succ --task-id $TaskId 2>$null | Out-Null }
} catch {}

if ($timedOut) {
    [Console]::Error.WriteLine("AGY TIMEOUT for $TaskId - treating as failure (exit=$ptyExit).")
    exit 124
}

if ($ptyFailed) {
    [Console]::Error.WriteLine("AGY FAILURE for $TaskId - wrapper/agy exited unsuccessfully (exit=$ptyExit).")
    exit $(if ($ptyExit -ne 0) { $ptyExit } else { 1 })
}

if ([string]::IsNullOrWhiteSpace($out)) {
    [Console]::Error.WriteLine("AGY EMPTY OUTPUT for $TaskId - treating as failure (no silent success).")
    exit 3
}

Write-Host "[dispatch_agy] $TaskId result:" -ForegroundColor Green
Write-Host $out
if ($OutFile) { Set-Content $OutFile $out -Encoding UTF8 }
exit 0

<#
.SYNOPSIS
    Reliable non-interactive Codex dispatch for AOA, with health
    preflight, hard timeout, fail-loud no-op detection, and live dashboard telemetry.

.DESCRIPTION
    Root cause of earlier "codex hangs / does nothing" (found 2026-06-26): codex was
    invoked with a working-dir path containing spaces passed through `-C`
    with a SPACE) plus a multi-word prompt as a positional arg. PowerShell/Start-Process
    split those into separate tokens, so codex died with `unexpected argument` or hung.

    This wrapper invokes codex the CORRECT way:
      - prompt via STDIN (codex exec - reads instructions from stdin) -> no quoting bugs
      - working dir via -WorkingDirectory (handles the space) -> no `-C` space-arg
      - MCP servers disabled by default (-c mcp_servers={}) to isolate dispatch
      - --json streaming captured to a file -> real output even if killed
      - hard wall-clock kill, and exit-0-with-no-message treated as FAILURE

    Telemetry: writes a running/idle entry to dashboard/agent_activity.js via
    agent_telemetry.py so the dashboard shows codex-<role> on its task with elapsed time.

    Exit codes: 0 success; 2 codex backend unavailable (preflight failed);
    3 ran but produced no agent message; 124 hard timeout; else codex's own code.

.PARAMETER Prompt        Inline prompt text (use -PromptFile for large specs).
.PARAMETER PromptFile    Path to a file whose contents are the prompt (preferred for specs).
.PARAMETER WorkDir       Working directory (default: repository root).
.PARAMETER TaskId        Task id for telemetry.
.PARAMETER Role          codex-<role> for telemetry (qa|coder|security|researcher...). Default qa.
.PARAMETER Desc          Human-readable task description for the dashboard.
.PARAMETER TimeoutSeconds Hard wall-clock limit. Default 600.
.PARAMETER OutFile       Optional path to write the final agent message.
.PARAMETER SkipPreflight Skip the CODEX_OK health probe.
.PARAMETER EnableMcp     Keep MCP servers enabled (default: disabled).

.EXAMPLE
    .\dispatch_codex.ps1 -PromptFile scratchpad\spec.md -TaskId DASH-SPLIT -Role qa -Desc "QA dashboard split"
#>

param(
    [string]$Prompt,
    [string]$PromptFile,
    [string]$WorkDir = "",
    [string]$TaskId = "ad-hoc",
    [string]$Role = "qa",
    [string]$Desc = "codex task",
    [int]$TimeoutSeconds = 0,
    [string]$OutFile,
    [ValidateSet('low','medium','high')][string]$Effort = 'medium',
    [switch]$SkipPreflight,
    [switch]$EnableMcp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "aoa_config.ps1")
if (-not $WorkDir) { $WorkDir = Get-AoaConfigValue "paths.workdir" }
if ($TimeoutSeconds -eq 0) { $TimeoutSeconds = [int](Get-AoaConfigValue "timeouts.codex_dispatch_seconds") }
$CodexCmd = Get-AoaConfigValue "cli.codex"
$PythonCmd = Get-AoaConfigValue "cli.python"
if (-not (Get-Command $CodexCmd -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("Codex executable not found: $CodexCmd (set AOA_CODEX_CMD)")
    exit 2
}

$TelemetryPy = Join-Path $PSScriptRoot "agent_telemetry.py"
$AgentId = "codex-$Role"

function Invoke-CodexRaw {
    param([string]$PromptPath, [int]$HardTimeoutSec)

    $so = [System.IO.Path]::GetTempFileName()
    $se = [System.IO.Path]::GetTempFileName()

    $argList = @("exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check")
    if (-not $EnableMcp) { $argList += @("-c", "mcp_servers={}") }
    $argList += @("-c", "model_reasoning_effort=$Effort")   # PTME: per-task effort
    $argList += "-"   # read prompt from stdin

    $code = -1
    try {
        $p = Start-Process -FilePath $CodexCmd -ArgumentList $argList `
            -RedirectStandardInput $PromptPath `
            -RedirectStandardOutput $so -RedirectStandardError $se `
            -WorkingDirectory $WorkDir -NoNewWindow -PassThru
        $null = $p.Handle   # keep the process handle so ExitCode is available after timed waits
        $done = $p.WaitForExit($HardTimeoutSec * 1000)
        if (-not $done) {
            # Grace window: codex may have finished real work and be mid-flush of its
            # final summary message. Give it a short extra window before the hard kill.
            $graceSec = 45
            $done = $p.WaitForExit($graceSec * 1000)
        }
        if (-not $done) {
            & taskkill /PID $p.Id /T /F 2>$null | Out-Null
            $null = $p.WaitForExit(5000)   # confirm the tree died + redirected I/O flushed before harvest
            $code = 124
        }
        else { $code = $p.ExitCode }
    }
    catch { $code = -1 }

    $stdout = Get-Content $so -Raw -ErrorAction SilentlyContinue
    $stderr = Get-Content $se -Raw -ErrorAction SilentlyContinue
    Remove-Item $so, $se -Force -ErrorAction SilentlyContinue

    # Parse the last agent_message text out of the JSONL stream.
    $msg = ""
    if ($stdout) {
        foreach ($line in ($stdout -split "`n")) {
            $line = $line.Trim()
            if (-not $line.StartsWith("{")) { continue }
            try { $o = $line | ConvertFrom-Json } catch { continue }
            if ($o.type -eq "item.completed" -and $o.item -and $o.item.type -eq "agent_message") {
                $msg = [string]$o.item.text
            }
        }
    }
    return [pscustomobject]@{ ExitCode = $code; Message = $msg; Stdout = $stdout; Stderr = $stderr }
}

# --- RATE-WALL PRECHECK (cheap, before the paid probe) ---
# Reads codex's primary(5h)/secondary(weekly) windows from its session JSONL. If a window is
# saturated, refuse to dispatch and print the local reset time - so we know WHEN codex returns
# instead of burning a probe and failing blind. (scripts/rate_wall_watchdog.py)
if (-not $SkipPreflight) {
    $rateWall = Join-Path $PSScriptRoot "rate_wall_watchdog.py"
    if (Test-Path $rateWall) {
        $rw = & $PythonCmd $rateWall should-dispatch --engine codex 2>&1
    }
    if ((Test-Path $rateWall) -and $LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(@"
CODEX RATE-LIMITED - not dispatching. $rw
Re-dispatch after the reset time above. (Tip: route this task to agy/claude meanwhile.)
"@)
        exit 2
    }
}

# --- HEALTH PREFLIGHT ---
if (-not $SkipPreflight) {
    Write-Host "[dispatch_codex] preflight: probing codex..." -ForegroundColor Cyan
    $pf = [System.IO.Path]::GetTempFileName()
    Set-Content $pf "Reply with exactly the token: CODEX_OK" -Encoding UTF8
    $probe = Invoke-CodexRaw -PromptPath $pf -HardTimeoutSec 90
    Remove-Item $pf -Force -ErrorAction SilentlyContinue
    if ($probe.Message -notmatch "CODEX_OK") {
        [Console]::Error.WriteLine(@"
CODEX UNAVAILABLE - health probe returned no CODEX_OK.
ExitCode=$($probe.ExitCode). Stderr tail:
$($probe.Stderr)
Likely a rate-limit/auth problem (not invocation). Check 'codex' login / usage. Refusing to dispatch.
"@)
        exit 2
    }
    Write-Host "[dispatch_codex] preflight OK (CODEX_OK)" -ForegroundColor Green
}

# --- resolve prompt to a file, with a token-efficiency preamble ---
$efficiency = @"
EFFICIENCY DIRECTIVE (conserve weekly usage): work at the LOWEST effort that does the job
well. Go straight to the task; minimal file exploration, no restating the prompt, no padded
explanations. Keep your final message concise: results + verdicts + exact paths only. If a
task is trivial, do not over-reason it.

"@
if ($PromptFile) {
    if (-not (Test-Path $PromptFile)) { Write-Error "PromptFile not found: $PromptFile"; exit 1 }
    $body = Get-Content (Resolve-Path $PromptFile).Path -Raw
}
elseif ($Prompt) { $body = $Prompt }
else { Write-Error "Provide -Prompt or -PromptFile"; exit 1 }

$promptPath = [System.IO.Path]::GetTempFileName()
Set-Content $promptPath ($efficiency + $body) -Encoding UTF8
$promptIsTemp = $true

# --- telemetry: mark running (surface failures; stale dashboard is a real signal) ---
try {
    & $PythonCmd $TelemetryPy start --agent $AgentId --task $TaskId --desc $Desc --model "gpt-5.4" --role $Role --effort "high" --reason "dispatched" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "[dispatch_codex] WARN telemetry start exited $LASTEXITCODE - dashboard may be stale" -ForegroundColor Yellow }
} catch { Write-Host "[dispatch_codex] WARN telemetry start threw: $_" -ForegroundColor Yellow }

Write-Host "[dispatch_codex] running $AgentId on $TaskId (hard timeout ${TimeoutSeconds}s)..." -ForegroundColor Cyan
$result = Invoke-CodexRaw -PromptPath $promptPath -HardTimeoutSec $TimeoutSeconds
if ($promptIsTemp) { Remove-Item $promptPath -Force -ErrorAction SilentlyContinue }

# --- telemetry: mark idle ---
$finalStatus = if ($result.ExitCode -eq 0 -and $result.Message) { "done" } else { "failed" }
try {
    & $PythonCmd $TelemetryPy stop --agent $AgentId --status $finalStatus | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "[dispatch_codex] WARN telemetry stop exited $LASTEXITCODE - entry may be stuck running" -ForegroundColor Yellow }
} catch { Write-Host "[dispatch_codex] WARN telemetry stop threw: $_" -ForegroundColor Yellow }

# --- learning loop: log the outcome so the agent-strength table learns from every dispatch ---
try {
    $succ = if ($result.ExitCode -eq 0 -and $result.Message) { 1 } else { 0 }
    $learningLoop = Join-Path $PSScriptRoot "learning_loop.py"
    if (Test-Path $learningLoop) { & $PythonCmd $learningLoop record --engine codex --role $Role --success $succ --task-id $TaskId 2>$null | Out-Null }
} catch {}

Write-Host "[dispatch_codex] codex exit $($result.ExitCode), status=$finalStatus" -ForegroundColor ($(if ($finalStatus -eq "done") { "Green" } else { "Yellow" }))
if ($result.Message) {
    Write-Host "[dispatch_codex] ---- codex message ----" -ForegroundColor Gray
    Write-Host $result.Message
    if ($OutFile) { Set-Content $OutFile $result.Message -Encoding UTF8; Write-Host "[dispatch_codex] message written to $OutFile" -ForegroundColor Cyan }
}

# --- FAIL-LOUD ---
if ($result.ExitCode -eq 0 -and [string]::IsNullOrWhiteSpace($result.Message)) {
    [Console]::Error.WriteLine("CODEX RUN PRODUCED NO AGENT MESSAGE (silent no-op) - treating as FAILURE. Stderr: $($result.Stderr)")
    exit 3
}
exit $result.ExitCode

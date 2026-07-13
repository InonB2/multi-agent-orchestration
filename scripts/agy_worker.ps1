<#
.SYNOPSIS
    Persistent agy worker — RUN THIS IN YOUR INTERACTIVE agy TERMINAL and leave it open.

.DESCRIPTION
    agy only works attached to a real TTY (it no-ops headless from Claude Code's tools).
    This worker runs in YOUR interactive terminal (which has a TTY), polls a queue folder,
    runs each task through agy, and writes the result back. Dispatchers drop tasks into the
    queue and read the results — making agy a 3rd pipeline agent without needing a TTY on my side.

    Usage (in your agy terminal):
        $env:TERM='xterm'; pwsh -File .\scripts\agy_worker.ps1
    Leave it running. Ctrl+C to stop.
#>
param(
    [string]$QueueDir  = "",
    [string]$ResultDir = "",
    [string]$AgePath   = "",
    [string]$WorkDir   = "",
    [int]$PollSeconds  = 0
)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "aoa_config.ps1")
if (-not $QueueDir) { $QueueDir = Get-AoaConfigValue "paths.agy_queue" }
if (-not $ResultDir) { $ResultDir = Get-AoaConfigValue "paths.agy_results" }
if (-not $AgePath) { $AgePath = Get-AoaConfigValue "cli.agy" }
if (-not $WorkDir) { $WorkDir = Get-AoaConfigValue "paths.workdir" }
if ($PollSeconds -eq 0) { $PollSeconds = [int](Get-AoaConfigValue "timeouts.agy_poll_seconds") }
$PythonCmd = Get-AoaConfigValue "cli.python"
$env:TERM = "xterm"
$TelemetryPy = Join-Path $PSScriptRoot "agent_telemetry.py"
New-Item -ItemType Directory -Force -Path $QueueDir, $ResultDir | Out-Null

Write-Host "[agy_worker] started. Polling $QueueDir every ${PollSeconds}s. Ctrl+C to stop." -ForegroundColor Cyan
while ($true) {
    $jobs = Get-ChildItem $QueueDir -Filter *.json -ErrorAction SilentlyContinue | Sort-Object LastWriteTime
    foreach ($j in $jobs) {
        $task = $null
        try { $task = Get-Content $j.FullName -Raw | ConvertFrom-Json } catch { Write-Host "[agy_worker] bad job $($j.Name): $_" -ForegroundColor Red; Remove-Item $j.FullName -Force; continue }
        $id = $task.id; $role = if ($task.role) { $task.role } else { "content" }
        $agent = "agy-$role"
        Write-Host "[agy_worker] running $id ($($task.desc))" -ForegroundColor Yellow
        try { & $PythonCmd $TelemetryPy start --agent $agent --task $id --desc $task.desc --model "gemini" --role $role --effort "medium" --reason "agy bridge" | Out-Null } catch {}
        $wd = if ($task.workdir) { $task.workdir } else { $WorkDir }
        $out = ""
        try {
            Push-Location $wd
            $out = & $AgePath --print $task.prompt 2>&1 | Out-String
            Pop-Location
        } catch { $out = "[agy_worker] agy threw: $_" }
        $resultPath = Join-Path $ResultDir ("$id.md")
        Set-Content $resultPath $out -Encoding UTF8
        try { & $PythonCmd $TelemetryPy stop --agent $agent --status "done" | Out-Null } catch {}
        Remove-Item $j.FullName -Force -ErrorAction SilentlyContinue
        Write-Host "[agy_worker] done $id -> $resultPath ($($out.Length) chars)" -ForegroundColor Green
    }
    Start-Sleep -Seconds $PollSeconds
}

[CmdletBinding()]
param(
    [string]$PythonCommand,
    [string]$Engines,
    [switch]$NonInteractive,
    [switch]$SkipAuth,
    [switch]$NoOpen,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not $PythonCommand) {
    foreach ($candidate in @('python', 'python3', 'py')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            $PythonCommand = $candidate
            break
        }
    }
}
if (-not $PythonCommand) {
    Write-Error 'Python 3.8 or newer was not found on PATH. Install Python, then rerun install.ps1.'
    exit 2
}

$versionCode = 'import sys; raise SystemExit(0 if sys.version_info >= (3,8) else 2)'
& $PythonCommand -c $versionCode
if ($LASTEXITCODE -ne 0) {
    Write-Error 'Python 3.8 or newer is required.'
    exit 2
}

$arguments = @((Join-Path $root 'scripts/install.py'), '--root', $root)
if ($Engines) { $arguments += @('--engines', $Engines) }
if ($NonInteractive) { $arguments += '--non-interactive' }
if ($SkipAuth) { $arguments += '--skip-auth' }
if ($NoOpen) { $arguments += '--no-open' }
if ($DryRun) { $arguments += '--dry-run' }

& $PythonCommand @arguments
exit $LASTEXITCODE

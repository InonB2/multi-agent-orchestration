# Shared PowerShell bridge to scripts/config_loader.py. Python owns parsing,
# validation, precedence, and install-root-relative path resolution.
$script:AoaRepoRoot = Split-Path -Parent $PSScriptRoot

function Get-AoaConfigValue {
    param([Parameter(Mandatory = $true)][string]$Key)
    $python = if ($env:AOA_PYTHON_CMD) { $env:AOA_PYTHON_CMD } else { "python" }
    $value = & $python (Join-Path $PSScriptRoot "config_loader.py") aoa-get $Key
    if ($LASTEXITCODE -ne 0) { throw "Unable to resolve AOA configuration key '$Key'" }
    return "$value".Trim()
}

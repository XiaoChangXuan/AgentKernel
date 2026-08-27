$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot "minicode_task_manager_reset"
$target = Join-Path $PSScriptRoot "minicode_task_manager"

if (-not (Test-Path -LiteralPath $source)) {
    throw "Missing pristine challenge copy: $source"
}

if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
}

Copy-Item -LiteralPath $source -Destination $target -Recurse
Write-Host "Restored MiniCode task manager challenge at $target"

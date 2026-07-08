param(
    [switch]$Commit
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$argsList = @("manage.py", "reset_testing_data")
if ($Commit) {
    $argsList += "--commit"
}

py @argsList
py manage.py check

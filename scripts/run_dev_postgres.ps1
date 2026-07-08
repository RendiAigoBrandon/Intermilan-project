$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        throw ".env tidak ditemukan di $Path"
    }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
        $key, $value = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim(), "Process")
    }
}

function Ensure-PostgresService {
    $service = Get-Service | Where-Object { $_.Name -match "postgresql" -or $_.DisplayName -match "PostgreSQL" } | Sort-Object Name | Select-Object -First 1
    if (-not $service) {
        Write-Warning "Service PostgreSQL tidak ditemukan. Pastikan PostgreSQL sudah berjalan manual."
        return
    }
    if ($service.Status -ne "Running") {
        try {
            Start-Service -Name $service.Name
            Write-Host "PostgreSQL service started: $($service.Name)"
        } catch {
            Write-Warning "Gagal start PostgreSQL service. Buka PowerShell sebagai Administrator atau aktifkan service PostgreSQL secara manual."
        }
    }
}

Import-DotEnv (Join-Path $ProjectRoot ".env")
$env:PGCLIENTENCODING = "UTF8"

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1")) {
    . (Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1")
}

Ensure-PostgresService

python manage.py check
python manage.py runserver

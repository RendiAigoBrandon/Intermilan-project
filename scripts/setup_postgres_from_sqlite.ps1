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

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupDir = Join-Path $ProjectRoot "backups\sqlite"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$sqlitePath = Join-Path $ProjectRoot "db.sqlite3"
$sqliteBackup = Join-Path $backupDir "db.sqlite3.backup.$timestamp"
$dumpPath = Join-Path $backupDir "sqlite_dump_$timestamp.json"
$postgresBackupDir = Join-Path $ProjectRoot "backups\postgres"
$postgresBackup = Join-Path $postgresBackupDir "postgres_before_sqlite_load_$timestamp.json"

if (-not (Test-Path $sqlitePath)) {
    throw "SQLite source tidak ditemukan: $sqlitePath"
}

Copy-Item -LiteralPath $sqlitePath -Destination $sqliteBackup -Force
Write-Host "SQLite backup: $sqliteBackup"

$env:DATABASE_ENGINE = "sqlite"
$env:DATABASE_NAME = "db.sqlite3"
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --exclude sessions --exclude admin.logentry --indent 2 -o $dumpPath
if ($LASTEXITCODE -ne 0) { throw "dumpdata SQLite gagal." }
Write-Host "SQLite dump: $dumpPath"

Import-DotEnv (Join-Path $ProjectRoot ".env")
$env:PGCLIENTENCODING = "UTF8"

New-Item -ItemType Directory -Force -Path $postgresBackupDir | Out-Null
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --exclude sessions --exclude admin.logentry --indent 2 -o $postgresBackup
if ($LASTEXITCODE -ne 0) { throw "Backup PostgreSQL gagal." }
Write-Host "PostgreSQL backup sebelum load: $postgresBackup"

python manage.py migrate
if ($LASTEXITCODE -ne 0) { throw "migrate PostgreSQL gagal." }
python manage.py flush --noinput
if ($LASTEXITCODE -ne 0) { throw "flush PostgreSQL gagal." }
python manage.py loaddata $dumpPath
if ($LASTEXITCODE -ne 0) { throw "loaddata PostgreSQL gagal." }
python manage.py repair_satker_names --commit
if ($LASTEXITCODE -ne 0) { throw "repair_satker_names gagal." }
python manage.py check
if ($LASTEXITCODE -ne 0) { throw "check gagal." }

Write-Host "Setup PostgreSQL dari SQLite selesai."

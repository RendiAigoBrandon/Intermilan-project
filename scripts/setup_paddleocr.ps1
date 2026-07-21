$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements-paddleocr.txt"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $Python)) {
    throw "Virtual environment tidak ditemukan: $Python"
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r $Requirements

if (Test-Path $EnvFile) {
    $Content = Get-Content $EnvFile -Raw
    if ($Content -match "(?m)^OCR_ENABLE_PADDLEOCR=") {
        $Content = $Content -replace "(?m)^OCR_ENABLE_PADDLEOCR=.*$", "OCR_ENABLE_PADDLEOCR=true"
        Set-Content -Path $EnvFile -Value $Content -Encoding UTF8
    } else {
        Add-Content -Path $EnvFile -Value "`nOCR_ENABLE_PADDLEOCR=true" -Encoding UTF8
    }
} else {
    Set-Content -Path $EnvFile -Value "OCR_ENABLE_PADDLEOCR=true" -Encoding UTF8
}

& $Python -c "import paddle, paddleocr; print('PaddleOCR aktif:', paddle.__version__, paddleocr.__version__)"
& $Python manage.py check

Write-Host "PaddleOCR selesai dipasang dan OCR_ENABLE_PADDLEOCR=true."

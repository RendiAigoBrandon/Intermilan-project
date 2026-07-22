@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment tidak ditemukan: .venv\Scripts\python.exe
    echo Jalankan dari root repository yang memiliki folder .venv.
    exit /b 1
)

set FLAGS_use_mkldnn=0
set PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0
set OCR_FORCE_IMAGE_FOR_SCANNED_DOCS=true
set OCR_PRIMARY_ENGINE_ORDER=text,tesseract
set OCR_PADDLE_FALLBACK_ENGINE_ORDER=text,paddleocr

".venv\Scripts\python.exe" -c "import sys; print('[INTERMILAN] Python:', sys.executable)"
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" manage.py runserver --noreload
endlocal

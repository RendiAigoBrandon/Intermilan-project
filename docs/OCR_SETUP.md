# OCR Setup INTERMILAN

Dokumen ini menjelaskan setup OCR untuk parser SPM, DRPP, KW, dan Paket SPM. OCR bersifat fallback: sistem selalu mencoba text layer PDF dulu, lalu Tesseract, lalu PaddleOCR jika diaktifkan.

## Engine Order

Default `.env`:

```env
OCR_ENGINE_ORDER=text,tesseract,paddleocr
OCR_ENABLE_PADDLEOCR=true
OCR_ENABLE_CLOUD=false
```

Urutan tersebut berarti:

1. `text`: baca text layer PDF dengan PyMuPDF, pdfplumber, dan pypdf/PyPDF2 jika tersedia.
2. `tesseract`: render PDF ke image dengan PyMuPDF, lakukan preprocessing Pillow, lalu OCR Tesseract.
3. `paddleocr`: opsional dan dilewati jika `OCR_ENABLE_PADDLEOCR=false`.

Cloud OCR disiapkan sebagai placeholder dan tetap OFF secara default.

## Setup Tesseract Windows

1. Install Tesseract OCR binary untuk Windows secara terpisah dari package Python.
2. Pastikan folder instalasi Tesseract ada di `PATH`.
3. Cek dari terminal:

```powershell
tesseract --version
```

4. Pasang language data Indonesia (`ind.traineddata`) dan English (`eng.traineddata`) di folder `tessdata`.
5. OCR INTERMILAN mencoba bahasa berikut secara berurutan:

```text
ind+eng
ind
eng
```

Jika `ind` belum tersedia, sistem otomatis mencoba `eng`.

## Dependency Python

Install dependency ke virtual environment project, bukan ke Python global:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Dependency OCR utama wajib tersedia di `.venv` aktif:

```text
PyMuPDF
pdfplumber
pytesseract
pypdf
Pillow
```

Jangan klaim OCR berhasil hanya karena package ada di Python global. OCR dianggap siap hanya jika package tersebut bisa di-import dari interpreter project:

```powershell
.\.venv\Scripts\python.exe -c "import pytesseract; print('pytesseract OK')"
.\.venv\Scripts\python.exe -c "import fitz; print('PyMuPDF OK')"
.\.venv\Scripts\python.exe -c "import pdfplumber; print('pdfplumber OK')"
.\.venv\Scripts\python.exe -c "import pypdf; print('pypdf OK')"
```

Jalankan server Django dengan interpreter `.venv` yang sama:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Jika command import di atas gagal, install ulang requirements ke `.venv` terlebih dahulu. Jika package Python sudah OK tetapi OCR tetap gagal pada scan, cek Tesseract binary Windows.

## PaddleOCR 3.x

PaddleOCR dipakai sebagai fallback untuk halaman yang hasil Tesseract-nya kosong
atau confidence-nya rendah. Install ke virtual environment project dengan file
dependency khusus agar instalasi dasar tetap ringan:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-paddleocr.txt
```

Atau jalankan setup Windows satu langkah dari root project:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_paddleocr.ps1
```

Pastikan `.env` berisi:

```env
OCR_ENABLE_PADDLEOCR=true
OCR_PADDLEOCR_DEVICE=cpu
OCR_PADDLEOCR_DOC_ORIENTATION=true
OCR_PADDLEOCR_DOC_UNWARPING=false
OCR_PADDLEOCR_TEXTLINE_ORIENTATION=true
```

Implementasi mendukung API PaddleOCR 3.x (`predict`) dan tetap menerima format
hasil 2.x untuk instalasi lama. Model PaddleOCR dapat diunduh saat pemakaian
pertama, sehingga proses pertama biasanya lebih lama daripada proses berikutnya.

## Cloud OCR Opsional

Cloud OCR tidak aktif secara default:

```env
OCR_ENABLE_CLOUD=false
OCR_CLOUD_PROVIDER=
```

Placeholder variabel tersedia untuk integrasi masa depan:

```env
GOOGLE_DOCUMENT_AI_ENABLED=false
GOOGLE_DOCUMENT_AI_PROJECT_ID=
GOOGLE_DOCUMENT_AI_LOCATION=
GOOGLE_DOCUMENT_AI_PROCESSOR_ID=
AZURE_DOCUMENT_INTELLIGENCE_ENABLED=false
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=
AZURE_DOCUMENT_INTELLIGENCE_KEY=
```

Jangan commit credential, key, atau secret ke repo.

## Status Preview

Parser mengembalikan status:

- `parsed_text`: text layer PDF cukup baik.
- `parsed_ocr`: OCR image dipakai.
- `needs_manual_review`: file terbaca sebagian tetapi butuh koreksi.
- `failed`: tidak ada engine yang dapat membaca dokumen.

Preview Paket SPM dan DRPP menampilkan engine terbaik, status, confidence, dan warning singkat. Detail OCR ada di bagian collapsible `Detail OCR`.

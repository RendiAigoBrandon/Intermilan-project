# Antigravity Runbook: OCR Paket SPM Document Graph

## Branch

```bash
git fetch origin
git switch feature/paket-spm-document-graph
git pull origin feature/paket-spm-document-graph
```

## Tujuan

Jalankan dan verifikasi arsitektur `document-graph-v1` untuk fitur Paket SPM. Jangan menambah regex yang mengacu pada nomor SPM sampel, nama PDF tertentu, warna kertas, nomor halaman tetap, atau nominal tertentu.

## Persiapan lingkungan Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Pastikan Tesseract terpasang. Contoh `.env`:

```env
OCR_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_ENGINE_ORDER=text,tesseract,paddleocr
OCR_FORCE_IMAGE_FOR_SCANNED_DOCS=true
OCR_ENABLE_PADDLEOCR=false
OCR_TESSERACT_LANGS=ind+eng,eng
OCR_CLASSIFY_DPI=150
OCR_TABLE_DPI=250
OCR_CACHE_ENABLED=true
```

Periksa bahasa Tesseract:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

Jika `ind` belum tersedia, sistem harus otomatis mencoba `eng`. Jangan mengubah kode menjadi wajib `ind+eng` tanpa fallback.

## Pemeriksaan Django

```powershell
python manage.py check
python manage.py test apps.paket_spm.test_package_graph -v 2
python manage.py test apps.paket_spm -v 2
```

## Uji PDF nyata

Gunakan berkas berikut dari folder pengujian pengguna:

1. `SPM NOMOR 00182A.pdf`
2. `SPM NOMOR 00140T.pdf`
3. `SPM NOMOR 00195A(1).pdf`

Bersihkan cache OCR lama sebelum pengujian setelah perubahan parser:

```powershell
Get-ChildItem -Recurse -Directory -Filter .ocr_cache | Remove-Item -Recurse -Force
```

Jalankan server:

```powershell
python manage.py runserver
```

Upload satu PDF pada fitur Paket SPM. Periksa `parsed_data` atau panel teknis dan pastikan:

1. `architecture` bernilai `document-graph-v1`.
2. `document_graph.nodes` memuat klasifikasi setiap halaman.
3. Halaman SPM, SPP, detail, DRPP, SSP, dan lampiran tidak dicampur menjadi satu tipe file.
4. `transaction_source` memakai `DRPP` ketika DRPP memiliki item valid.
5. Tanpa DRPP, transaksi memakai tabel resmi `DETAIL_SPP_SPM_SP2D` atau lampiran COA yang telah tervalidasi total.
6. SSP, faktur, invoice, BAST, dan dokumen pendukung tidak menjadi transaksi baru.
7. Total `kw_items` harus sama dengan bruto SPM. Ketidaksesuaian harus menghasilkan `PERLU_REVIEW`, bukan nilai nol dan bukan baris rekaan.
8. Output akhir tetap mengisi 15 kolom D_K: Helper, Akun, SP2D Bulan, Cara Pembayaran, Nomor SPM, Tanggal SPM, Jenis SPM, No. Kuitansi, No. DRPP, Deskripsi, Nilai Bruto, Nilai Netto, Pembebanan, FP, dan PPh21.

## Prompt kerja untuk Antigravity

```text
Anda sedang bekerja pada repository RendiAigoBrandon/Intermilan-project, branch feature/paket-spm-document-graph.

Sumber kebenaran:
1. Audit arsitektur OCR Paket SPM.
2. Git diff dan kode aktual pada branch ini.
3. Hasil PDF nyata, bukan asumsi agent sebelumnya.

Tugas:
1. Jalankan python manage.py check.
2. Jalankan python manage.py test apps.paket_spm.test_package_graph -v 2.
3. Jalankan seluruh test apps.paket_spm.
4. Pastikan Tesseract ditemukan dan catat bahasa yang tersedia.
5. Hapus cache .ocr_cache lama.
6. Uji upload SPM NOMOR 00182A.pdf, SPM NOMOR 00140T.pdf, dan SPM NOMOR 00195A(1).pdf.
7. Audit parsed_data.document_graph, transaction_source, validation, kw_items, dan hasil 15 kolom D_K.
8. Perbaiki hanya masalah umum berbasis layout, klasifikasi halaman, cell extraction, penggabungan antardokumen, dan validasi bisnis.
9. Dilarang menambah regex atau if khusus nomor 00182A, 00140T, 00195A, nama PDF, warna kertas, nomor halaman tetap, atau nominal sampel.
10. SSP, faktur, invoice, BAST, dan dokumen pendukung tidak boleh membuat transaksi baru.
11. Jika parser tidak dapat membuktikan baris secara struktural, pertahankan file dan beri PERLU_REVIEW. Jangan mengisi nol dan jangan membuat baris rekaan.
12. Jangan mengubah main. Commit perbaikan ke branch feature/paket-spm-document-graph dan laporkan commit SHA, test yang dijalankan, hasil setiap PDF, jumlah baris, total bruto, total netto, sumber transaksi, dan seluruh warning yang tersisa.

Kriteria selesai:
1. Tidak ada exception pada upload dan preview.
2. Setiap halaman memiliki document_type, confidence, evidence, dan nomor halaman.
3. Total transaksi cocok dengan dokumen induk atau status PERLU_REVIEW menjelaskan selisihnya.
4. Hasil tidak bergantung pada database lama atau nama file.
5. Semua 15 kolom D_K terisi dari bukti dokumen yang dapat dilacak.
```

# Status Parser Paket SPM

Tanggal audit: 2026-07-15

| Area spesifikasi | Status | Bukti kode | Catatan |
|---|---|---|---|
| Identity probe sebelum OCR penuh | Partial | `apps/paket_spm/services.py::probe_package_identity`, `apps/paket_spm/views.py::paket_spm_list` | Sudah dipanggil sebelum parser penuh, tetapi probe ZIP masih berbasis filename/native text ringan dan belum membaca header OCR rendah semua file. |
| Page classifier per halaman | Partial | `apps/core/parsers.py::classify_page_types`, `apps/core/ocr.py::classify_page` | Sudah ada klasifikasi per halaman dan manifest dasar, namun confidence/alasan klasifikasi belum kaya untuk semua tipe. |
| Parser registry per jenis dokumen | Partial | `apps/core/parsers.py::DOCUMENT_PARSER_REGISTRY` | SPM/DRPP/KW memakai extractor; invoice/faktur/BAST/SSP/SP2D masih review-only. |
| Parser tabel cell/TSV/koordinat | Partial | `parse_detail_sp2d_rows_by_grid`, `parse_detail_sp2d_rows_by_crop`, `parse_position_detail_items` | Production SPM memakai parser v2 untuk detail SP2D; DRPP Bukti Pengeluaran masih fallback text OCR yang dikontrol validasi total. |
| Pemisahan SPM/SP2D/DRPP/Lampiran COA/KW/support | Partial | `classify_document`, `classify_page_types`, `parse_paket_spm_zip` | Support tidak jadi transaksi; klasifikasi beberapa dokumen masih perlu diperluas. |
| Relasi SPM -> DRPP -> KW -> support | Partial | `parse_paket_spm_zip`, `merge_followup_into_existing_dk` | DRPP mewariskan item KW; dokumen support belum terhubung granular per item. |
| Larangan KW tunggal tanpa DRPP | Implemented | `has_standalone_kw_without_drpp`, `paket_spm_list`, `parse_paket_spm_zip` | KW tunggal menjadi review dan tidak membuat D_K. |
| Existing D_K sebelum full OCR | Partial | `probe_package_identity`, `parsed_from_identity_probe` | Exact D_K skip full parser; confidence konflik masih perlu diperkaya. |
| Data meragukan menjadi Perlu Review | Partial | `build_package_decision`, `evaluate_document_status` | Parser tabel gagal dan KW standalone diblokir; beberapa parser lama masih memberi `needs_manual_review` tapi belum selalu membawa field confidence. |
| Tidak fallback legacy flat text untuk auto-commit | Partial | `spm_table_parser_needs_review`, `build_transaction_rows_from_package` | SPM parser v2 gagal diblokir; DRPP text parser masih dipakai dengan validasi total karena belum ada cell parser DRPP penuh. |
| Preview DRPP editable | Partial | `templates/paket_spm/preview.html`, `paket_spm_preview` | Preview rows bisa diedit untuk D_K; parent/item DRPP khusus belum lengkap sesuai spec. |
| Blind test | Missing | - | Belum ada corpus blind test yang stabil di repo. |
| Accordion D_K | Missing | - | Ditunda sesuai instruksi agar fokus P0 parser. |

## Production Flow Audit

1. Upload masuk `apps/paket_spm/views.py::paket_spm_list`.
2. File disimpan sementara ke `MEDIA_ROOT/tmp`, banyak file dibungkus ZIP.
3. `probe_package_identity` mencari kandidat nomor dan exact D_K sebelum full OCR.
4. Jika exact D_K aman, `parsed_from_identity_probe` membuat draft tanpa row builder.
5. Jika tidak, PDF tunggal diklasifikasi dengan `classify_document`; ZIP diproses `parse_paket_spm_zip`.
6. Parser memakai `extract_pdf_text` untuk native/OCR, `classify_page_types` untuk halaman, dan `DOCUMENT_PARSER_REGISTRY` untuk jenis dokumen.
7. SPM memanggil `parse_position_detail_items`; detail valid harus berasal dari parser tabel v2.
8. `build_package_decision` menentukan status dan aksi commit.
9. Preview memanggil `build_transaction_rows_from_package(save=False)` hanya jika decision mengizinkan.
10. Commit `create_from_package` membuat D_K; `link_existing/update_existing` hanya mengaitkan dokumen ke D_K existing.

## Validasi 2026-07-15

### Regression Corpus Nyata

| Dokumen | Jalur | Hasil |
|---|---|---|
| DRPP 00029 | `parse_drpp_pdf` real Tesseract | 12 item KW, total 30.195.422, total tercetak cocok, status `parsed_ocr`. |
| DRPP 00030 KW 00209 | `parse_drpp_pdf` real Tesseract | 1 item KW `00209/KW/019937/2026`, akun 521811, total 3.558.750, invoice/faktur/BAST tidak menjadi item. |
| SPM 00135T | `probe_package_identity` sebelum full parser | Exact D_K ditemukan untuk `00135T`; jalur ini tidak membangun ulang D_K existing. Integration test grid OCR 00135T tetap lulus sebagai regression parser detail. |

### Blind Test

Blind file: `media/tmp/red9z_aw.upload.pdf`.

Hasil: `needs_manual_review`, DRPP 00025, 2 item, total item 6.423.800, total tercetak 3.423.800. Karena total tidak cocok, sistem tidak menganggap valid otomatis. Ini sesuai aturan "benar atau Perlu Review"; bukan silent wrong.

### Command Terakhir

- `python manage.py check`: lulus.
- `python manage.py makemigrations --check --dry-run`: no changes.
- `python manage.py test apps.paket_spm --keepdb -v 1`: 51 test lulus, 2 skip opt-in untuk OCR upload lambat.
- Integration real OCR eksplisit:
  - `test_real_drpp_00029_and_00030_use_production_ocr_without_support_as_items`: lulus.
  - `test_real_00135t_detail_table_uses_grid_ocr`: lulus.
- `git diff --check`: lulus, hanya warning CRLF Windows.

### Sisa Missing/Partial

- DRPP Bukti Pengeluaran belum sepenuhnya cell/TSV parser; saat ini text OCR dibatasi halaman DRPP dan divalidasi total.
- Relasi granular invoice/faktur/BAST ke item KW masih belum lengkap; support sudah dipisahkan agar tidak menjadi transaksi.
- Identity probe belum OCR header rendah multi-file untuk semua kondisi konflik.
- Full upload production test untuk PDF 31 halaman masih opt-in karena lambat; parser langsung dan integration OCR lulus.
- Preview DRPP editable parent/item belum lengkap sesuai seluruh spesifikasi.

# 11 - Confirmed Requirements 2026-06-30

## UI

- UI legacy Django disetujui sementara sebagai baseline.
- Jangan redesign UI besar-besaran tanpa persetujuan baru.

## Tahap 2

- Fokus database foundation, production readiness, role access, dan import data awal.
- Sumber utama: SQLite legacy dan Excel awal.
- Development boleh SQLite.
- Production disiapkan untuk PostgreSQL, MySQL sebagai opsi.
- Import harus aman: dry-run, skip duplicate, dan replace hanya jika dikonfirmasi.
- Google Drive pada Tahap 2 disimpan sebagai metadata/link, belum integrasi API aktif.

## Larangan Sementara

- Parser PDF.
- OCR.
- Upload Paket SPM penuh.
- Upload KW massal.
- Drop table.
- Hapus database lama.
- Auto replace data import.

## Tahap 2.1 - Audit Integritas Data Import

- Audit integritas import wajib read-only.
- Jangan hapus, replace, drop, atau cleanup data sebelum ada persetujuan pengguna.
- Semua temuan duplikat, orphan, data dummy, warning tanggal, dan link belum match harus dilaporkan lebih dulu.
- Command audit resmi: `python manage.py audit_import_integrity`.
- Laporan audit Markdown disimpan di folder `document` untuk review pengguna.
- Rekomendasi perbaikan boleh dibuat, tetapi eksekusi perbaikan data menunggu persetujuan.

## Tahap 2.2 - Review Audit Data dan PostgreSQL Readiness

- Sumber audit utama adalah `docs/13_IMPORT_AUDIT_REPORT.md`.
- Halaman Review Data `/audit-data/` wajib read-only.
- Export CSV `/audit-data/export/` hanya membaca data audit dan tidak mengubah database.
- Tidak boleh ada tombol hapus, replace, cleanup, merge, atau fix otomatis yang aktif.
- PostgreSQL hanya disiapkan settings dan dokumentasinya.
- Database aktif development tidak boleh dipindahkan ke PostgreSQL sebelum disetujui.
- Tidak ada model baru dan migration baru pada Tahap 2.2.

## Tahap 2.3 - Role Access & Permission

- Role final: Admin, Operator Satker, Viewer.
- Admin dapat melihat/mengelola semua satker, Review Data, import, upload, edit, dan export.
- Operator Satker dapat edit/upload satker sendiri dan melihat monitoring lintas satker sebagai read-only.
- Viewer hanya read-only dan tidak dapat upload, edit, delete, import, cleanup, fix, atau akses Review Data.
- Permission harus divalidasi di backend, bukan hanya disembunyikan di UI.
- Tidak ada migration baru kecuali disetujui.
- Database aktif tetap SQLite development.

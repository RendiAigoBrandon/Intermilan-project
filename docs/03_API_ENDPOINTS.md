# 03 - API / URL Endpoints

Tahap 2 belum menambahkan REST API baru. Endpoint yang aktif masih berupa halaman Django internal.

## Halaman Data

- `/dashboard/`: dashboard statistik dari database.
- `/audit-data/`: halaman Review Data read-only untuk temuan audit import.
- `/audit-data/export/`: export CSV read-only daftar temuan audit import. Response `text/csv` dengan `Content-Disposition: attachment`.
- `/sp2d/`: daftar data mentah SP2D. Query: `q`, `status`.
- `/dk/`: daftar Detail Keuangan / D_K. Query: `q`, `satker`, `bulan`, `akun`, `kelengkapan`.
- `/monitoring/`: monitoring dokumen lintas satker. Query: `q`, `satker`, `bulan`, `status`.
- `/master-akun/`: master akun dari database.
- `/akun/`: ringkasan per akun dari D_K.
- `/documents/`: halaman arahan singkat; checklist dibuka dari tombol Checklist pada D_K.
- `/documents/<transaction_id>/`: detail checklist dokumen dan DRPP untuk satu transaksi.
- `/drpp/`: daftar DRPP. Dapat menerima query `?transaction_id=<id>` sebagai placeholder aman untuk review/upload DRPP per transaksi.
- `/reports/`: laporan/export placeholder.

## Management Commands

- `python manage.py import_legacy_sqlite --path "...sp2d_kk1300.sqlite"`
- `python manage.py import_excel_seed --path "...Database awal"`
- `python manage.py audit_import_integrity`
- `python manage.py inspect_active_data`
- `python manage.py create_dev_users`
- `python manage.py reset_testing_data --dry-run`
- `python manage.py reset_testing_data --commit`

Command import default dry-run. Gunakan `--commit` untuk import asli. Command audit dan `inspect_active_data` bersifat read-only. Command `create_dev_users` hanya untuk development saat `DEBUG=True`. Command `reset_testing_data` dipakai untuk membersihkan data transaksi/import testing, membuat backup sebelum commit, dan tidak menghapus user, profile, MasterAkun, atau ChecklistTemplate.

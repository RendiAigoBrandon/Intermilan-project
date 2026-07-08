# 05 - Feature List

## Selesai Tahap 2 Foundation

- Production database setting siap untuk SQLite/PostgreSQL/MySQL.
- Import SQLite legacy read-only.
- Import Excel seed read-only.
- Master Akun tersimpan sebagai model.
- Link Google Drive tersimpan sebagai model terpisah dari upload lokal.
- Dashboard/SP2D/D_K/Monitoring/Master Akun/Akun Keuangan membaca database.
- Duplicate import aman dengan default skip.
- Mode dry-run tersedia untuk import.
- Audit import read-only tersedia lewat management command.
- Halaman Review Data tersedia di `/audit-data/`.
- Export CSV temuan audit tersedia di `/audit-data/export/` dengan header `kategori,item,jumlah,detail,status_review,rekomendasi`.
- UI global Tahap 2.2 dirapikan untuk header kiri, filter/action bar compact, dan tabel `.data-table`.
- PostgreSQL readiness disiapkan di settings production dan dokumentasi deployment, tanpa switch database aktif development.
- Role access Tahap 2.3 tersedia untuk Admin, Operator Satker, dan Viewer.
- Helper permission backend tersedia di `apps/accounts/access.py`.
- Command development `create_dev_users` tersedia untuk membuat user contoh di lingkungan DEBUG.
- D_K menjadi pusat akses checklist; detail checklist memakai `/documents/<transaction_id>/`.
- `/documents/` hanya halaman arahan singkat agar tidak menduplikasi tabel D_K.
- Tombol DRPP pada D_K muncul hanya untuk transaksi yang punya No. DRPP, mengandung pola GU/GUP/UP/TUP/PTUP/KKP, atau sudah punya DRPPUpload/DRPPMatch.
- Inbox SP2D dan kelengkapan dokumen transaksi dibedakan: `/sp2d/` untuk SP2D raw yang belum cocok D_K, sedangkan D_K/Monitoring untuk checklist dokumen transaksi.
- Filter aktif tersedia pada `/sp2d/`, `/dk/`, dan `/monitoring/`.
- Inbox SP2D menampilkan kolom raw SP2D termasuk Tanggal Invoice, Jenis SPM, Jenis SP2D, Deskripsi, Status Detail, dan Aksi.
- Font UI memakai font lokal `Inter` dari `static/fonts/inter/` dengan fallback Arial/Helvetica.
- Tabel Dashboard, Monitoring, dan D_K memakai label kolom sesuai Excel dan horizontal scroll.
- Sidebar menu memiliki area scroll sendiri di bawah header.
- Dashboard MoM membaca D_K aktif per satker untuk bulan terpilih dengan filter GET tahun/bulan dan label scope role.
- Tooltip chart Dashboard menampilkan bulan, dataset, nominal rupiah, atau persentase.

## Belum Dikerjakan

- Parser PDF.
- OCR.
- Upload Paket SPM penuh.
- Upload KW massal.
- Integrasi Google Drive API aktif.
- REST API.
- Sumber resmi FA16 belum tersedia di database aktif; dataset FA16 Dashboard belum dihitung dari data asli.
## Upload Parser Preview-First

- Upload SP2D Excel membaca workbook asli dengan deteksi header otomatis pada 30 baris awal setiap sheet.
- Preview SP2D menampilkan sheet, header row, jumlah baris mentah, jumlah baris valid, kolom asli, mapping kolom, dan 10 contoh baris.
- Commit SP2D hanya aktif jika parser menemukan baris valid.
- Upload DRPP PDF tersedia dengan alur upload, parse, preview, commit, dan cancel.
- Upload Paket SPM menerima PDF SPM tunggal atau ZIP paket SPM, mengekstrak ZIP secara aman, mengklasifikasi PDF, dan menampilkan preview sebelum commit.
- Parser PDF mencoba text extraction lebih dulu. Jika PDF berupa scan dan dependency OCR belum tersedia, UI menampilkan status `needs_manual_review`, bukan error 500.

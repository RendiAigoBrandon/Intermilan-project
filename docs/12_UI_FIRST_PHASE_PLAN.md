# 12 — UI First Phase Plan

## Status Saat Ini

Fase UI utama selesai sementara. UI legacy Django sudah dianggap cukup stabil sebagai baseline Tahap 2.

Backup baseline UI stable:

- `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_ui_stable_20260630-154611`
- `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_ui_stable_after_toolbar_20260701`

Fokus berikutnya berpindah ke database foundation, production database readiness, role access, dan migrasi/import data awal. UI tidak boleh diubah besar-besaran tanpa persetujuan baru.

Baseline aktif terbaru adalah `intermilan_project_ui_stable_after_toolbar_20260701`. Header, sidebar, CSS legacy, toolbar/filter, tabel global, layout card, dan tombol/action bar tidak boleh diubah lagi sebelum ada instruksi baru.

Update layout global:

- Semua halaman internal memakai shell yang sama dari `templates/base.html`: `.app-shell`, `.page-container`, `.app-sidebar`, dan `.app-main`.
- Sidebar desktop memakai state `body.sidebar-collapsed`; saat tertutup konten utama melebar tanpa sisa kolom sidebar.
- Tablet/mobile memakai state `body.sidebar-open` sebagai drawer kiri dengan overlay.
- Spacing atas dikendalikan dari `static/css/layout.css` agar tidak berbeda antar halaman.

Update form/filter global:

- Filter halaman memakai `.filter-panel`, `.filter-grid`, `.filter-field`, `.filter-search`, dan `.filter-actions` agar tidak full width berlebihan di desktop.
- Form upload/detail memakai `.form-grid`, `.form-field`, `.form-control`, `.file-field`, dan `.form-actions`.
- Ringkasan transaksi memakai `.info-list`/`.detail-list` agar label dan value tidak saling menumpuk.
- Tabel memakai wrapping dan horizontal scroll global untuk menjaga kolom panjang tetap terbaca.
- Toolbar/action bar memakai `.toolbar-panel` dan `.toolbar-row` agar tombol filter/export tidak mengambang.
- Tabel besar memakai `.data-table` dan min-width khusus agar compact dengan horizontal scroll.
- Alur checklist dikembalikan ke D_K: tombol Checklist pada setiap transaksi membuka `/documents/<transaction_id>/`.
- Menu sidebar Checklist Dokumen & DRPP disembunyikan sementara agar tidak menduplikasi tabel D_K.

Tidak dikerjakan pada fase UI:

- migrasi database,
- parser PDF,
- OCR,
- Upload Paket SPM penuh,
- Upload KW massal,
- perubahan model/migration/database.

## Status Baseline 2026-07-01

- UI legacy disetujui sementara.
- Filter/action bar sudah dirapikan.
- Tabel global sudah compact.
- Database tetap SQLite development.
- Tidak ada model, migration, atau database yang berubah.

## Halaman yang Disesuaikan

- `/` Home legacy dengan hero INTERMILAN, logo BPS besar, tombol Buka Dashboard dan Upload SP2D, kontak WhatsApp/alamat kantor.
- `/accounts/login/` Login legacy dengan background gedung dan card centered.
- `/dashboard/` Dashboard legacy dengan metric cards, chart MoM, tabel Dashboard INTERMILAN, shortcut, cari akun, upload terakhir, SP2D perlu detail.
- `/sp2d/` Upload & Data Mentah SP2D.
- `/paket-spm/` Form Upload Paket SPM PDF/ZIP UI-only.
- `/monitoring/` Monitoring Dokumen dengan metric cards, filter, tabel monitoring.
- `/dk/` Database D_K Web dengan filter, tabel besar, tombol Checklist, serta tombol Upload/Lihat DRPP bersyarat.
- `/documents/` halaman arahan singkat; detail checklist aktif di `/documents/<transaction_id>/`.
- `/drpp/` Daftar DRPP UI-only, termasuk placeholder query `?transaction_id=<id>`.
- `/master-akun/` Master Akun.
- `/akun/` Pisah Per Akun.
- `/reports/`, `/peraturan/`, `/template/`, `/panduan-aplikasi/` placeholder legacy-style.

## Acceptance UI

UI dianggap siap review bila:

- header/sidebar/card/tombol/tabel mirip screenshot prototype,
- tidak ada checkbox menu terlihat,
- hamburger kecil normal,
- tidak ada konten atau brand dari project lain,
- check/test lulus,
- user menyetujui tampilan sebelum fitur backend berat dilanjutkan.

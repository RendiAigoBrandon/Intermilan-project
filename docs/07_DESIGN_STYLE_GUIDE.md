# 07 — Design Style Guide INTERMILAN Django

## Keputusan Style Aktif

UI Django wajib mengikuti style INTERMILAN lama/prototype, bukan UI workbook polos dan bukan redesign baru.

Status baseline terbaru:

- `intermilan_project_ui_stable_after_toolbar_20260701`
- UI legacy disetujui sementara setelah perapihan toolbar/filter dan tabel global.
- Jangan ubah header, sidebar, CSS legacy, toolbar/filter, tabel global, layout card, atau tombol/action bar sebelum ada instruksi baru.

Elemen yang dipertahankan:

- Header atas biru gradasi.
- Hamburger kecil normal di kiri header.
- Logo BPS dan tulisan INTERMILAN besar.
- Subtitle: `Instrumen Terintegrasi Monitoring Pengelolaan Keuangan`.
- User/admin pill di kanan atas.
- Body biru muda/abu muda.
- Card putih rounded besar dengan shadow lembut.
- Tombol utama orange.
- Tombol secondary biru muda.
- Tabel header biru muda.
- Sidebar/menu card putih dari kiri, menu aktif biru tua, logout merah muda.
- Home, Dashboard, Monitoring, D_K, Upload SP2D, Paket SPM, Checklist, Akun, dan Master Akun mengikuti screenshot prototype.

## Larangan

- Jangan redesign bebas.
- Jangan mengembalikan UI workbook polos.
- Jangan memakai checkbox toggle lama yang terlihat.
- Jangan hamburger raksasa.
- Jangan menambahkan konten, logo, kredensial, atau brand dari project lain.
- Jangan lanjut parser/OCR/Paket SPM penuh sebelum UI disetujui.

## CSS Aktif

CSS Django tetap modular:

- `static/css/base.css`
- `static/css/layout.css`
- `static/css/components.css`
- `static/css/pages.css`

Model visualnya mengikuti `style.css` legacy dan screenshot prototype. Toggle sidebar memakai button + `static/js/layout.js`, bukan checkbox lama.

## Font

- UI memakai font lokal `Inter`.
- Sumber font yang dipakai adalah Inter variable font open-source dari repo resmi Inter.
- File font berada di `static/fonts/inter/`.
- Font diterapkan global melalui `@font-face` di `static/css/base.css`.
- Font stack aktif: `"Inter", Arial, Helvetica, sans-serif`.

## Revisi Global Tahap 2.2

- Header tetap memakai gradasi biru legacy, tetapi branding diposisikan ke kiri setelah hamburger.
- Urutan header desktop: hamburger, logo BPS, INTERMILAN, subtitle kecil, user pill kanan.
- Subjudul halaman dibuat ringkas, profesional, dan operasional.
- Filter memakai pola `.filter-panel`, `.filter-grid`, `.filter-field`, dan `.filter-actions`.
- Tombol aksi halaman memakai `.page-actions` atau `.action-bar` agar tidak terpencar.
- Tabel operasional memakai `.data-table` dengan kolom bantu:
  - `.col-no`
  - `.col-satker`
  - `.col-akun`
  - `.col-bulan`
  - `.col-tanggal`
  - `.col-spm`
  - `.col-kuitansi`
  - `.col-drpp`
  - `.col-deskripsi`
  - `.col-nominal`
  - `.col-status`
- Header tabel dibuat lebih compact; nominal rata kanan dan tidak wrap; nomor dokumen/tanggal dibuat nowrap; deskripsi diberi ruang lebih besar dan wrap rapi.
- Filter desktop memakai flex-wrap agar field kecil dan tombol tetap sejajar selama ruang tersedia; stack satu kolom hanya untuk mobile.
- Filter dan tombol aksi halaman disatukan dalam `.toolbar-panel` dan `.toolbar-row`; action bar tidak boleh berdiri sendiri di kanan atas card dengan ruang kosong besar.
- Tabel banyak kolom tidak dipaksa masuk card. Gunakan min-width khusus seperti `.table-sp2d`, `.table-dk`, `.table-monitoring`, dan `.table-dashboard` dengan horizontal scroll pada `.table-wrap`.
- Kolom `Nama Satker` memakai `.col-nama-satker` agar identitas satker tidak pecah menjadi banyak baris.
- Pagination D_K memakai `.pagination-wrap`, `.pagination-info`, dan `.page-btn` sebagai link server-side; tombol `Next`/`Previous` tidak boleh bergantung pada JavaScript.

## Baseline Locked

Baseline UI stable after toolbar dikunci pada 2026-07-01. Perubahan berikutnya harus mempertahankan style legacy ini kecuali pengguna secara eksplisit meminta revisi UI baru.

## Revisi Tabel dan Scroll Setelah Tahap 2.3

- Sidebar tetap memakai visual legacy, tetapi scroll-nya mandiri: `.navstrip` sticky di bawah header dan memakai `overflow-y: auto`.
- Konten kanan tetap berada di `.app-main`; scrolling konten tidak menarik posisi sidebar.
- Tabel banyak kolom wajib memakai horizontal scroll pada `.table-wrap`.
- Min-width tabel diperbesar untuk label Excel: `.table-dk`, `.table-monitoring`, `.table-dashboard`, `.table-sp2d`, dan `.table-documents`.
- Kolom tambahan yang dipakai: `.col-helper`, `.col-cara-bayar`, `.col-jenis`, `.col-pembebanan`, dan `.col-percent`.
- Nominal memakai format Indonesia tanpa desimal, rata kanan, dan nowrap.
- Tanggal D_K memakai format tampilan `01 January 2026` jika data tanggal tersedia.
- D_K menjadi pusat akses Checklist dan DRPP. Menu sidebar `Checklist Dokumen & DRPP` disembunyikan sementara agar tidak menduplikasi tabel D_K.
- `/documents/` hanya halaman arahan singkat; detail visual checklist hanya muncul pada `/documents/<transaction_id>/`.
- Inbox SP2D memakai tabel raw SP2D, bukan kolom D_K; kolom D_K seperti Helper, Akun detail, DRPP, Pembebanan, FP, dan PPh21 tetap berada di D_K.

## Dashboard MoM

- Dashboard tetap mengikuti style legacy dan tidak memakai desain chart baru di luar prototype.
- Filter Dashboard memakai form compact dengan field Tahun, Bulan Fokus, tombol Tampilkan, dan Reset.
- Chart MoM mempertahankan gaya Excel/prototype: legend berwarna, horizontal scroll, grid line, label bulan miring, dan tooltip kecil.
- Tooltip chart wajib memakai `Inter` dan tidak mengubah warna/card/header/sidebar baseline.
- X-axis chart utama Dashboard memakai daftar satker `bps1300`, `bps1301`, dan seterusnya seperti prototype lama.
- Chart tren Januari-Desember tidak menjadi chart utama Dashboard; jika dibutuhkan nanti, posisinya sebagai chart tambahan.

## Revisi Font dan Tabel 2026-07-02

- Font legacy lama diganti menjadi Inter lokal agar tampilan mendekati referensi BMKG tanpa bergantung CDN.
- Font diterapkan ke body, header, sidebar, tombol, input, select, textarea, tabel, badge, card, filter, chart tooltip, login, dan pagination.
- Tabel operasional tetap memakai style legacy, tetapi header dibuat lebih lembut, border lebih tipis, hover lebih subtle, dan nominal memakai tabular numeric.
- Badge dan tombol aksi kecil dibuat lebih compact tanpa mengubah warna legacy.
- Filter tetap memakai struktur `.filter-panel`, `.filter-grid`, `.filter-field`, dan `.filter-actions` dengan label kecil semibold serta focus ring halus.

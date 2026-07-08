
## 2026-06-30 — UI Django dikembalikan ke style prototype INTERMILAN lama

### Konteks

Pengguna menegaskan fokus saat ini hanya UI. Screenshot prototype lama dan documentation pack v2 dijadikan acuan utama. Desain workbook polos/dashbord baru tidak boleh dilanjutkan.

### Keputusan

UI Django disesuaikan ke style prototype legacy:

- header biru gradasi,
- hamburger kecil,
- logo BPS + INTERMILAN besar,
- user pill kanan,
- sidebar menu card putih dengan active biru dan logout soft red,
- card rounded besar shadow lembut,
- tombol orange/secondary biru muda,
- tabel header biru muda,
- halaman Home/Dashboard/Monitoring/D_K/SP2D/Paket SPM/Checklist/Akun/Master Akun mengikuti screenshot.

### Alasan

Prioritas terbaru pengguna adalah UI harus terlihat seperti prototype INTERMILAN lama sebelum migrasi data dan fitur berat dilanjutkan.

### File kode terdampak

- `templates/base.html`
- `templates/accounts/login.html`
- `templates/core/home.html`
- `templates/core/dashboard.html`
- `templates/core/monitoring.html`
- `templates/core/master_akun.html`
- `templates/core/akun_index.html`
- `templates/core/reference.html`
- `templates/sp2d/list.html`
- `templates/dk/list.html`
- `templates/documents/checklist_overview.html`
- `templates/drpp/list.html`
- `templates/paket_spm/list.html`
- `templates/reports/index.html`
- `apps/core/views.py`
- `apps/core/urls.py`
- `intermilan_project/urls.py`
- `static/css/base.css`
- `static/css/layout.css`
- `static/css/components.css`
- `static/css/pages.css`

### Dokumen terdampak

- `docs/07_DESIGN_STYLE_GUIDE.md`
- `docs/12_UI_FIRST_PHASE_PLAN.md`
- `docs/10_DECISION_LOG.md`

### Validasi

- `python manage.py check`
- `python manage.py test`
- route smoke test UI

### Risiko / Next step

- UI masih perlu review visual langsung dari pengguna.
- Jangan lanjut parser/OCR/Upload Paket SPM penuh sebelum UI disetujui.

## 2026-06-30 — Perapihan Layout Global Sidebar

### Konteks

Setelah fungsi buka/tutup sidebar berjalan, pengguna meminta layout dimaksimalkan agar tidak ada ruang kosong kiri/atas atau jarak antar-section yang berbeda antar halaman.

### Keputusan

- `templates/base.html` menjadi sumber struktur layout untuk semua halaman internal melalui `.app-shell`, `.page-container`, `.app-sidebar`, dan `.app-main`.
- `static/css/layout.css` dibuat full-width untuk konten, mengurangi margin atas ganda, dan memakai drawer untuk tablet/mobile.
- State layout tetap memakai `body.sidebar-collapsed` untuk desktop dan `body.sidebar-open` untuk tablet/mobile.

### Batasan

Tidak ada perubahan model, database, migration, parser/OCR, Upload Paket SPM penuh, warna, font, card, tombol, header, logo, atau gaya visual utama.

## 2026-06-30 — Cleanup Global Filter, Form, Detail, dan Tabel

### Konteks

Pengguna meminta filter tidak full width panjang, form upload lebih rapi, ringkasan transaksi tidak numpuk, dan tabel konsisten di semua halaman.

### Keputusan

- Menambahkan class global `.filter-panel`, `.filter-grid`, `.filter-field`, `.filter-search`, `.filter-actions`.
- Menambahkan class global `.form-grid`, `.form-field`, `.form-control`, `.file-field`, `.form-actions`.
- Menggunakan `.info-list` untuk Ringkasan Transaksi agar label/value wrap rapi.
- Menguatkan table wrapping dan file input styling di `static/css/components.css`.

### Batasan

Tidak ada perubahan model, database, migration, parser/OCR, Upload Paket SPM penuh, label Excel, istilah Indonesia, atau gaya visual legacy utama.

## 2026-06-30 — UI Legacy Django Disetujui Sementara

### Konteks

Pengguna menyatakan UI INTERMILAN Django sudah cukup stabil dan sesuai prototype lama. Fase berikutnya berpindah ke Tahap 2: database foundation, production database readiness, role access, dan migrasi/import data awal.

### Keputusan

- UI legacy Django dianggap baseline stabil sementara.
- Sebelum Tahap 2 dimulai, project dibackup ke `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_ui_stable_20260630-154611`.
- Tahap 2 tidak boleh melakukan redesign UI besar, perubahan style legacy, parser PDF/OCR, Upload Paket SPM penuh, atau Upload KW massal.

### Batasan

Perubahan berikutnya difokuskan ke database, migration/import aman, role access, dan data asli muncul di UI.

## 2026-06-30 — Tahap 2 Database Foundation dan Import Data Awal

### Keputusan

- Menambahkan `dk.MasterAkun` untuk master akun database.
- Menambahkan `documents.DocumentDriveLink` untuk metadata/link Google Drive tanpa menghapus `DocumentUpload`.
- Menyiapkan settings development/production agar bisa memakai SQLite, PostgreSQL, atau MySQL via environment.
- Menambahkan command `import_legacy_sqlite` dan `import_excel_seed`.
- Command import default dry-run; import asli wajib memakai `--commit`; replace wajib memakai `--replace-confirmed`.

### Import Yang Dijalankan

- SQLite legacy dry-run penuh.
- SQLite legacy import asli dengan skip duplicate.
- Excel seed dry-run penuh.
- Excel seed import asli dengan skip duplicate.

### Count Akhir

- SP2D import batch: 43.
- SP2D raw: 1.874.
- D_K / TransactionDetail: 6.661.
- Master akun: 53.
- Checklist template: 601.
- Checklist status: 127.188.
- DocumentDriveLink: 4.060.
- DRPP upload: 1.
- DRPP item: 4.
- DRPP match: 4.
- Audit log legacy masuk: 10.

### Catatan

Excel import memunculkan warning openpyxl untuk beberapa cell tanggal dengan serial di luar batas; baris tetap diproses dan tanggal bermasalah diperlakukan kosong.

### Batasan

Tidak ada parser PDF/OCR, Upload Paket SPM penuh, Upload KW massal, drop table, hapus database lama, atau auto replace data.

## 2026-07-01 — Tahap 2.1 Audit Integritas Data Import

### Konteks

Data hasil import awal sudah masuk ke database development. Sebelum lanjut ke OCR/parser/Upload Paket SPM penuh, pengguna meminta audit read-only untuk memastikan data tidak bercampur dummy, tidak ada duplikat berbahaya, dan struktur import aman.

### Keputusan

- Menambahkan command `audit_import_integrity` sebagai audit read-only.
- Audit tidak menghapus, mengganti, drop table, atau memperbaiki data otomatis.
- Hasil audit diekspor ke `C:\Users\muall\Documents\INTERMILAN PROJECT\document\13_IMPORT_AUDIT_REPORT_20260701-084847.md`.
- Semua perbaikan data wajib dibuat sebagai rencana terpisah dan menunggu persetujuan pengguna.

### Temuan Utama

- D_K/TransactionDetail berjumlah 6.661 baris: 5.359 cocok dengan SQLite legacy dan 1.302 adalah tambahan non-legacy yang perlu review.
- Ditemukan 1 grup kandidat duplikat D_K berdasarkan satker, SPM, kuitansi, DRPP, akun, nilai bruto, dan nilai netto.
- MasterAkun berjumlah 53 karena ada tambahan kode `51xxxx` dibanding legacy 52 akun.
- ChecklistTemplate berjumlah 601 karena mengikuti tabel legacy yang berisi kombinasi template/pattern/kategori; distinct `nama_dokumen` berjumlah 168.
- ChecklistStatus berjumlah 127.188 dengan 0 orphan dan 0 duplikat transaction_detail + nama_dokumen.
- DocumentDriveLink berjumlah 4.060: 3.994 matched ke TransactionDetail dan 66 belum matched.
- Warning tanggal Excel ditemukan pada `KK_1308.xlsx`, sheet `D_K`, cell `F256-F280`, kolom Tanggal SPM.
- Potensi data dummy/test ditemukan pada 2 `TransactionDetail.deskripsi` dan 1 `SP2DRaw.deskripsi`.

### Batasan

Tidak ada hapus data, replace data, drop table, migration baru, perubahan UI besar, OCR/parser, atau implementasi Upload Paket SPM penuh.

## 2026-07-01 — Tahap 2.2 Review Audit Data dan PostgreSQL Readiness

### Konteks

Audit import sudah selesai dan perlu ditampilkan di aplikasi agar temuan bisa direview tanpa membuka command line. Pengguna juga meminta PostgreSQL readiness tanpa memindahkan database aktif development.

### Keputusan

- Sumber audit utama ditetapkan hanya `docs/13_IMPORT_AUDIT_REPORT.md`.
- FilLanjut rehearsal berhasil sampai import dan count PostgreSQL.

Path Excel yang benar
C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal\drive-download-20260615T061835Z-3-001

Di folder itu ditemukan 21 file Excel:
INTERMILAN.xlsx + KK_1300.xlsx sampai KK_1377.xlsx.

Command import yang dipakai

py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" import_excel_seed --path "C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal\drive-download-20260615T061835Z-3-001" --commit
Status

Django confirmed pakai PostgreSQL: django.db.backends.postgresql
Database: intermilan_rehearsal
User: intermilan_user
Import berhasil.
Output import: Excel files: 21
Warning tanggal Excel tetap muncul untuk KK_1308.xlsx cell F256-F280, sama seperti audit sebelumnya.
Password rehearsal hanya dipakai sebagai environment variable sementara dan tidak boleh ditulis ke file project.
Count PostgreSQL setelah import

Model	PostgreSQL
SP2DRaw	0
TransactionDetail / D_K	5.684
Master Akun	42
Checklist Template	0
Checklist Status	0
DocumentDriveLink	3.081
DRPP Upload	0
DRPP Item	0
DRPP Match	0
D_K PostgreSQL
Distinct satker:
1300, 1301, 1302, 1303, 1304, 1305, 1306, 1307, 1308, 1309, 1310, 1311, 1312, 1371, 1372, 1373, 1374, 1375, 1376, 1377

D_K per bulan:

Jan: 246
Feb: 1.608
Mar: 1.474
Apr: 1.461
Mei: 795
Jun: 99
Jul: 1
Perbandingan dengan SQLite baseline

Model	SQLite baseline	PostgreSQL	Selisih
SP2DRaw	1.874	0	-1.874
D_K	6.661	5.684	-977
Master Akun	53	42	-11
Checklist Template	601	0	-601
Checklist Status	127.188	0	-127.188
DocumentDriveLink	4.060	3.081	-979
DRPP Upload	1	0	-1
DRPP Item	4	0	-4
DRPP Match	4	0	-4
Penyebab selisih kemungkinan besar

PostgreSQL fresh import ini hanya menjalankan import_excel_seed dari Database awal.zip.
Command existing mengimpor D_K dan sheet Upload dari file KK_13xx.
INTERMILAN.xlsx saat ini hanya diaudit sebagai reference, belum dipakai untuk isi checklist/monitoring penuh.
SP2D raw test memang tidak diimport, sesuai instruksi.
ChecklistTemplate/ChecklistStatus/DRPP di SQLite berasal dari sumber/import lain, bukan dari jalur fresh seed ini.
Validasi basic
check dengan PostgreSQL:

System check identified no issues (0 silenced).
PostgreSQL sudah siap untuk tahap validasi route berikutnya, terutama Dashboard/D_K/Monitoring berbasis D_K. Tapi belum layak switch final karena SP2D raw, checklist, dan DRPP belum ikut terisi pada rehearsal ini.

SQLite belum dihapus, db.sqlite3 belum dioverwrite, dan belum switch final ke PostgreSQL.e audit timestamp atau rekap lain tidak dijadikan sumber utama Tahap 2.2.
- Menambahkan halaman read-only `Review Data` pada route `/audit-data/`.
- Menambahkan export CSV read-only pada route `/audit-data/export/`.
- Export CSV hanya membaca ringkasan temuan audit, tidak mengubah database.
- PostgreSQL hanya disiapkan lewat settings dan dokumentasi deployment; database aktif development tetap SQLite.

### Batasan

Tidak ada model baru, migration baru, cleanup data, hapus data, replace data, drop table, switch database ke PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, atau redesign UI besar.

### Revisi UI Global Tahap 2.2

- Header global dirapikan agar branding INTERMILAN berada di kiri setelah hamburger, dengan user pill tetap di kanan.
- Subjudul halaman dipersingkat agar lebih operasional.
- Filter dan tombol aksi dirapikan memakai `.filter-panel`, `.filter-grid`, `.filter-actions`, `.page-actions`, dan `.action-bar`.
- Tabel utama dirapikan memakai `.data-table` dan class kolom konten.
- Export CSV Review Data diperbaiki dengan filename bertanggal dan kolom `kategori,item,jumlah,detail,status_review,rekomendasi`.

### Revisi Layout Filter dan Tabel

- Filter global diubah ke flex-wrap desktop agar field dan tombol tidak turun ketika ruang kanan masih tersedia.
- Filter dashboard Tahun/Bulan/Tampilkan dibuat grid 3 kolom pada desktop.
- Tombol aksi halaman disatukan ke `.toolbar-panel`/`.toolbar-row` agar tidak mengambang di kanan atas card.
- Tabel data besar diberi min-width khusus dan horizontal scroll: `.table-sp2d`, `.table-dk`, `.table-monitoring`, `.table-dashboard`.
- Kolom identitas seperti Nama Satker, nomor dokumen, tanggal, dan nominal dibuat nowrap agar row tidak terlalu tinggi.

## 2026-07-01 — Baseline UI Stable After Toolbar

### Konteks

Pengguna menyatakan UI INTERMILAN Django sudah oke untuk sementara setelah perapihan header, sidebar, toolbar/filter, action bar, tabel global, dan layout card.

### Keputusan

- Versi UI saat ini dikunci sebagai baseline stable terbaru.
- Backup dibuat di `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_ui_stable_after_toolbar_20260701`.
- Header, sidebar, CSS legacy, toolbar/filter, tabel global, layout card, dan tombol/action bar tidak boleh diubah lagi sebelum ada instruksi baru.
- UI legacy disetujui sementara.
- Filter/action bar sudah dirapikan.
- Tabel global sudah dibuat compact dengan horizontal scroll untuk tabel besar.
- Database aktif tetap SQLite development.
- Tidak ada model, migration, atau database yang berubah pada penguncian baseline ini.

### Batasan Berikutnya

Jangan lanjut fitur lain atau mengubah UI baseline sebelum pengguna meminta tahap berikutnya.

## 2026-07-01 — Tahap 2.3 Role Access & Permission

### Konteks

Baseline UI stable sudah dikunci. Tahap ini berfokus pada helper permission, satker scope, proteksi Review Data, user development, dan test role tanpa mengubah model/database.

### Keputusan

- Menambahkan helper permission eksplisit di `apps/accounts/access.py`.
- Review Data `/audit-data/` dan `/audit-data/export/` dibatasi Admin.
- Monitoring tetap bisa dibaca lintas satker, tetapi edit/checklist mengikuti admin atau pemilik satker.
- Tombol upload/export/checklist disembunyikan atau disabled untuk role yang tidak berhak.
- Menambahkan command `create_dev_users` untuk development saat `DEBUG=True`.
- Menambahkan test permission helper dan akses Review Data admin-only.

### Batasan

Tidak ada model baru, migration baru, perubahan database, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, Upload KW massal, atau perubahan besar UI baseline.

## 2026-07-01 — Revisi UI/Alur/Tabel Setelah Role Access

### Konteks

Pengguna meminta perbaikan dalam batas UI/alur/tabel: `/documents/` tidak boleh langsung membuka transaksi `00074T`, sidebar harus scroll sendiri, tabel utama harus menampilkan label Excel, nama satker, dan format angka/tanggal rapi.

### Keputusan

- `/documents/` menjadi halaman daftar transaksi/checklist.
- Detail checklist memakai route `/documents/<transaction_id>/`.
- Tombol Checklist pada D_K diarahkan ke transaksi yang sesuai, bukan route default.
- Sidebar dibuat sticky di bawah header dengan scroll internal.
- Dashboard INTERMILAN, Monitoring, dan D_K mengikuti header kolom Excel.
- Tabel besar tetap horizontal scroll dan tidak dipaksa sempit.
- Nominal ditampilkan dengan format Indonesia tanpa desimal; tanggal D_K memakai format `01 January 2026` jika tersedia.
- Nama satker ditampilkan dari `sp2d_raw.satker_name` atau lookup SP2D berdasarkan `satker_code`; jika belum ada, tampil `-`.

### Batasan

Tidak ada model baru, migration baru, perubahan database, cleanup data, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, atau Upload KW massal.

## 2026-07-02 — Verifikasi Ulang Filter dan Audit Data Aktif

### Konteks

Manual check menunjukkan dropdown Bulan/Satker dan search masih perlu dibuktikan dari database aktif. Audit read-only dijalankan ulang terhadap `db.sqlite3`.

### Keputusan

- Database aktif terbukti memiliki banyak satker dan bulan Januari-Juli; dropdown tidak boleh dibatasi ke Januari atau satu satker.
- Filter bulan tetap memakai daftar Januari-Desember.
- Dropdown satker mengikuti data aktif dan permission, dengan label `kode - nama/-`.
- Search D_K diperkuat agar mencakup `pembebanan` dan nama satker melalui lookup SP2D aktif.
- Search Monitoring mencakup kode satker, nama satker, bulan, status, dan BAR/% completed pada hasil ringkasan.
- Test filter tetap memeriksa perubahan isi hasil, bukan hanya HTTP 200.

### Batasan

Tidak ada model baru, migration baru, perubahan database, cleanup data, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, atau Upload KW massal.

## 2026-07-01 — Filter Aktif, Inbox SP2D Raw, dan Font Lokal

### Konteks

Pengguna meminta perbaikan kecil tanpa redesign: filter harus bekerja, Inbox SP2D harus berbeda dari D_K, status detail SP2D harus mudah dibaca, dan font lokal dari paket font diterapkan global.

### Keputusan

- Font lokal saat itu memakai paket font legacy yang diberikan.
- Keputusan font tersebut diganti pada 2026-07-02 menjadi Inter lokal.
- `/sp2d/` menampilkan data raw SP2D dengan kolom Status Detail, bukan kolom transaksi D_K.
- Status Detail SP2D ringan: `Sudah Ada D_K` bila cocok dengan D_K berdasarkan nomor SPM/invoice, selain itu `Belum Ada Detail`.
- Filter `/sp2d/`, `/dk/`, dan `/monitoring/` memakai query parameter dan mengubah hasil tabel.
- Ringkasan Excel dipetakan ke Dashboard/Monitoring; D_K tetap detail transaksi; Inbox SP2D tetap raw SP2D.

### Batasan

Tidak ada model baru, migration baru, perubahan database, cleanup data, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, atau Upload KW massal.

## 2026-07-01 — Revisi Alur Checklist Kembali Berpusat di D_K

### Konteks

Pengguna memutuskan bahwa Checklist Dokumen & DRPP tidak boleh menjadi menu/tabel terpisah yang menduplikasi D_K. Alur awal dikembalikan: user membuka D_K, lalu memilih Checklist per transaksi.

### Keputusan

- D_K menjadi pusat akses checklist dokumen.
- Tombol Checklist di setiap baris D_K selalu mengarah ke `/documents/<transaction_id>/`.
- `/documents/` hanya halaman arahan singkat ke D_K dan tidak menampilkan daftar transaksi.
- Menu sidebar `Checklist Dokumen & DRPP` disembunyikan sementara.
- Helper `requires_drpp(transaction)` ditambahkan untuk menentukan apakah tombol DRPP perlu tampil.
- Tombol DRPP tampil bila transaksi punya No. DRPP, jenis/cara pembayaran mengandung GU/GUP/UP/TUP/PTUP/KKP, atau sudah punya DRPPUpload/DRPPMatch.
- Route DRPP tahap ini memakai placeholder aman `/drpp/?transaction_id=<id>`.
- Status belum lengkap dipisahkan: Inbox SP2D untuk SP2D raw yang belum cocok D_K, sedangkan D_K/Monitoring untuk kelengkapan dokumen transaksi.

### Batasan

Tidak ada model baru, migration baru, perubahan database, cleanup data, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, atau Upload KW massal.

## 2026-07-02 — Perbaikan Dashboard, Chart MoM, dan Scope Role

### Konteks

Manual check Dashboard menunjukkan kartu statistik dapat terlihat seperti hanya data `operator_1300`, dropdown bulan hanya Januari, dan chart MoM hanya satu cluster. Pengguna meminta perbaikan khusus Dashboard tanpa melanjutkan OCR/parser/Upload Paket SPM penuh.

### Keputusan

- Dashboard memakai scope eksplisit sesuai role: Admin semua satker, Operator Satker satker sendiri, Viewer semua satker read-only.
- Label scope ditampilkan pada Dashboard agar angka kartu tidak ambigu.
- Filter Dashboard memakai GET `tahun` dan `bulan`; dropdown bulan selalu Januari-Desember.
- Chart MoM dibangun dari `TransactionDetail.bulan_sp2d` database aktif dan selalu menampilkan Januari-Desember.
- Bulan tanpa data ditampilkan 0.
- Data aktif import awal tercatat Januari-Juli 2026; Agustus-Desember menunggu import data lanjutan.
- Dataset FA16 resmi belum tersedia di database aktif; nilai FA16 ditampilkan 0 dan diberi catatan agar tidak dipalsukan dari D_K.
- Tooltip chart menampilkan bulan, dataset, nominal Rupiah, atau persentase.

### Batasan

Tidak ada model baru, migration baru, perubahan database, cleanup data, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, Upload KW massal, atau redesign UI besar.

## 2026-07-02 — Koreksi Chart Utama Dashboard Kembali Per Satker

### Konteks

Pengguna menegaskan chart utama Dashboard harus mengikuti acuan dashboard lama: X-axis berupa daftar satker `bps1300`, `bps1301`, dan seterusnya. Chart Januari-Desember tidak boleh menggantikan chart utama.

### Keputusan

- Chart utama Dashboard dikembalikan menjadi per satker pada bulan terpilih.
- Filter Tahun/Bulan tetap memakai GET dan menentukan bulan chart.
- Dataset tetap empat warna seperti prototype: hijau persentase, kuning realisasi kumulatif, merah realisasi bulan ini, biru FA16.
- Audit field database aktif menunjukkan belum ada field/model FA16 persisten selain label/mapping Excel; dataset biru tetap tampil bernilai 0 dengan catatan.
- Admin dan Viewer melihat chart semua satker.
- Operator Satker melihat chart semua satker secara read-only, tetapi kartu/tabel operasional tetap mengikuti scope satker sendiri.
- Tooltip chart menampilkan satker, bulan/tahun, dataset, dan nilai.

### Batasan

Tidak ada model baru, migration baru, perubahan database, cleanup data, switch PostgreSQL, OCR, parser PDF, Upload Paket SPM penuh, Upload KW massal, atau redesign UI besar.

## 2026-07-02 — PostgreSQL Fresh Import Rehearsal Preparation

### Konteks

Pengguna meminta rehearsal fresh import ke PostgreSQL lokal tanpa menyimpan atau hardcode password PostgreSQL ke repo. PostgreSQL 18.4 terpasang dan service `postgresql-x64-18` berjalan.

### Keputusan

- Backup baseline dibuat ke `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_before_postgresql_fresh_import_20260702`.
- `Database awal.zip` diekstrak ke `C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal`.
- File Excel yang ditemukan mencakup `INTERMILAN.xlsx` dan `KK_1300.xlsx` sampai `KK_1377.xlsx`.
- `psql` tersedia, PostgreSQL menerima koneksi, tetapi koneksi sebagai `postgres` tanpa password ditolak dengan `fe_sendauth: no password supplied`.
- Karena terminal tool tidak dapat menerima password interaktif secara aman, pembuatan database/user PostgreSQL tidak dijalankan otomatis.
- Command manual tanpa password hardcoded dicatat di `docs/deployment.md`.

### Batasan

Tidak ada password PostgreSQL yang ditulis ke repo, README, atau docs. SQLite development tidak dihapus, `db.sqlite3` tidak dioverwrite, database aktif belum switch final ke PostgreSQL, dan tidak ada cleanup data/OCR/parser/Upload Paket SPM penuh.

## 2026-07-02 — PostgreSQL Clean Baseline dan Dashboard Monitoring_Combine

### Konteks

Pengguna menetapkan bahwa PostgreSQL rehearsal tidak perlu mengejar count SQLite development. SQLite development kemungkinan memuat data test/legacy/manual seperti SP2D raw, checklist progress, DRPP, Upload Paket SPM, atau eksperimen lain.

### Keputusan

- PostgreSQL rehearsal diarahkan sebagai clean baseline database dari `Database awal.zip`.
- `Database awal.zip` berisi `INTERMILAN.xlsx` dan `KK_1300.xlsx` sampai `KK_1377.xlsx`.
- Hasil clean seed PostgreSQL saat ini:
  - SP2DRaw: 0.
  - D_K / TransactionDetail: 5.684.
  - Master Akun: 42.
  - ChecklistTemplate: 0.
  - ChecklistStatus: 0.
  - DocumentDriveLink: 3.081.
  - DRPP Upload/Item/Match: 0.
- Count PostgreSQL tidak wajib sama dengan SQLite development.
- Selisih D_K 977 dari SQLite development tidak disebabkan checklist; D_K dan checklist adalah jenis data berbeda.
- File SP2D raw test, data upload/DRPP/checklist test, dan data eksperimen SQLite lama tidak dibawa ke clean baseline.

### Audit Dashboard Excel

- `INTERMILAN.xlsx` sheet `Dashboard` memakai formula query ke `Monitoring_Combine`.
- Formula utama mengambil `Monitoring_Combine!A:O` dan memfilter `Bulan SP2D` dari cell filter bulan serta `TA` dari cell filter tahun.
- X-axis chart Dashboard adalah daftar satker seperti `bps1300`, `bps1301`, dan seterusnya.
- `Monitoring_Combine` berisi 480 baris ringkasan: 20 satker x 12 bulan x 2 tahun.
- Tahun yang tersedia di `Monitoring_Combine`: 2025 dan 2026.
- Dataset Dashboard berasal dari kolom:
  - biru: `Realisasi FA 16 Detil Bulan ini (di isi satker)`;
  - merah: `Realisasi Intermilan Bulan ini`;
  - kuning: `Realisasi Intermilan s.d Bulan Ini`;
  - hijau: `Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)`.

### Rekomendasi

Tambahkan model ringan `MonitoringSummary` pada tahap berikutnya untuk menyimpan seed resmi `Monitoring_Combine`. Dashboard web sebaiknya memakai `MonitoringSummary` jika tersedia dan hanya fallback ke hitung D_K bila summary belum ada.

### Batasan

Belum ada model/migration baru pada keputusan ini. SQLite belum dihapus, PostgreSQL belum switch final, dan tidak ada OCR/parser/Upload Paket SPM penuh.

## 2026-07-02 — Implementasi MonitoringSummary Hidup

### Konteks

Pengguna menyetujui model `MonitoringSummary`, tetapi menegaskan tabel ini tidak boleh menjadi snapshot Excel mati. Baseline awal tetap berasal dari `INTERMILAN.xlsx` sheet `Monitoring_Combine`, lalu ringkasan harus bisa di-refresh dari data web harian.

### Keputusan

- Menambahkan model `core.MonitoringSummary`.
- Unique constraint: `satker_code + bulan_number + tahun`.
- Metadata sumber ditambahkan lewat `source`, `last_refreshed_at`, dan `notes`.
- Source yang didukung: `excel_seed`, `calculated`, `manual`, dan `mixed`.
- Menambahkan command `import_monitoring_summary` untuk upsert baseline dari `INTERMILAN.xlsx -> Monitoring_Combine`.
- Menambahkan service `refresh_monitoring_summary()` dan command `refresh_monitoring_summary`.
- Refresh menghitung:
  - `intermilan_bulan_ini` dari sum D_K bulan terkait;
  - `intermilan_sd_bulan_ini` dari sum D_K sampai bulan terkait;
  - `persen_realisasi` dari Intermilan terhadap FA16 bila FA16 > 0.
- Refresh tidak mengubah `fa16_bulan_ini`.
- Dashboard membaca `MonitoringSummary` jika tersedia, dan fallback ke D_K hanya bila summary belum ada.
- PostgreSQL rehearsal sudah dimigrate untuk `core.0001_initial` dan baseline `MonitoringSummary` berhasil diimport sebanyak 480 baris dari `Monitoring_Combine`.

### Command

```powershell
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" import_monitoring_summary --path "C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal\drive-download-20260615T061835Z-3-001" --commit
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" refresh_monitoring_summary --tahun 2026 --bulan 6
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" refresh_monitoring_summary --all
```

### Batasan

FA16 tetap data pembanding tersendiri dan tidak dihitung dari D_K. Tidak ada import SP2D raw test, DRPP test, ChecklistStatus progress test, Upload Paket SPM test, cleanup SQLite, atau switch final PostgreSQL.

## 2026-07-02 — Validasi Route PostgreSQL dan User Semua Satker

### Konteks

Setelah `MonitoringSummary` berhasil diimport ke PostgreSQL rehearsal, pengguna meminta validasi route utama, Dashboard/Monitoring berbasis summary, dan user operator untuk seluruh satker aktif.

### Keputusan

- `create_dev_users` mendukung opsi `--all-satker`.
- Opsi `--all-satker` mengambil distinct satker dari `MonitoringSummary` dan `TransactionDetail`, bukan range hardcoded.
- PostgreSQL rehearsal memiliki 20 operator satker aktif:
  `1300, 1301, 1302, 1303, 1304, 1305, 1306, 1307, 1308, 1309, 1310, 1311, 1312, 1371, 1372, 1373, 1374, 1375, 1376, 1377`.
- Login berhasil untuk `admin`, `operator_1300`, `operator_1377`, dan `viewer`.
- Route PostgreSQL berhasil dibuka: `/`, `/dashboard/`, `/sp2d/`, `/dk/`, `/documents/<transaction_id>/`, `/monitoring/`, `/akun/`, dan `/audit-data/` sebagai admin.
- `/audit-data/` tetap 403 untuk viewer dan operator.
- Dashboard PostgreSQL membaca `MonitoringSummary`.
- Monitoring PostgreSQL membaca `MonitoringSummary`.
- Baseline `MonitoringSummary` belum di-refresh massal agar angka Excel tetap utuh.

### Batasan

PostgreSQL masih rehearsal dan belum switch final. SQLite belum dihapus. Tidak ada import SP2D raw test, DRPP test, ChecklistStatus progress test, OCR/parser, atau Upload Paket SPM penuh.

## 2026-07-02 — Revisi Font UI ke Inter dan Polish Tabel

### Konteks

Pengguna meminta fokus khusus pada font UI, tabel, filter, dan badge tanpa mengubah logic Dashboard/Monitoring/D_K, permission, database, model, atau migration.

### Keputusan

- Font UI diganti menjadi Inter lokal dengan stack `"Inter", Arial, Helvetica, sans-serif`.
- File font berada di `static/fonts/inter/`.
- `@font-face` Inter variable dipanggil dari `static/css/base.css`.
- Referensi font legacy lama di kode dan dokumentasi dibersihkan.
- Tabel global dipoles agar lebih mirip gaya dashboard data BMKG: header lembut, border tipis, hover subtle, nominal rata kanan dengan tabular numeric, deskripsi tetap wrap, dan kolom kode/dokumen tetap nowrap.
- Filter tetap memakai pola legacy yang sudah disetujui, hanya label, input, select, focus ring, badge, dan tombol aksi kecil yang dibuat lebih konsisten.

### Batasan

Tidak ada perubahan model, migration, database, permission, Dashboard/Monitoring/D_K logic, OCR/parser, Upload Paket SPM penuh, Upload KW massal, atau switch PostgreSQL final.

## 2026-07-02 — D_K Server-Side Pagination dan Dashboard Summary Table

### Konteks

Manual check menunjukkan D_K hanya tampil `1-20 dari 50 data`, padahal database aktif berisi ribuan `TransactionDetail`. Dashboard juga masih berpotensi mencampur tabel ringkasan Monitoring dengan preview D_K detail.

### Temuan

- Penyebab D_K hanya 50 data adalah `apps/dk/views.py` melakukan slice `queryset[:50]` sebelum pagination.
- Slice tersebut membuat total display dan tombol pagination tidak mewakili total D_K sebenarnya.
- Database aktif SQLite development saat validasi berisi 6.661 `TransactionDetail`.
- Filter satker `1300` berisi 504 `TransactionDetail`, sehingga page size 20 menghasilkan 26 halaman.

### Keputusan

- D_K memakai server-side pagination Django.
- Queryset difilter berdasarkan permission, search, satker, bulan, akun, dan kelengkapan terlebih dahulu, lalu dipaginasi.
- Opsi `page_size`: 20, 50, dan 100.
- Link `Previous`, nomor halaman, dan `Next` memakai anchor normal dan mempertahankan query parameter filter.
- Command read-only `inspect_dk_pagination` ditambahkan untuk diagnosis DB backend, DB name, role/scope, total global, total scope, total filter, page size, jumlah page, dan current page.
- Tabel `Dashboard INTERMILAN` memakai `MonitoringSummary` sebagai ringkasan `Monitoring_Combine`.
- D_K detail di Dashboard hanya tampil sebagai `Preview Detail Keuangan` terbatas, sehingga tidak dikira sama dengan tabel ringkasan Excel.

### Batasan

Tidak ada perubahan model, migration, database, import data, OCR/parser, Upload Paket SPM penuh, Upload KW massal, cleanup SQLite, atau switch PostgreSQL final.

## 2026-07-02 — Koreksi Scope Dashboard dan Monitoring Excel

### Konteks

Pengguna meluruskan bahwa alur aplikasi tidak boleh diubah. D_K tetap pusat transaksi, Checklist dan DRPP tetap dibuka dari D_K, Upload & Inbox SP2D tetap alur upload SP2D, dan PostgreSQL belum switch final. Yang perlu diluruskan hanya isi, label, dan sumber data pada Dashboard dan Monitoring agar mengikuti Excel.

### Keputusan

- D_K server-side pagination dari keputusan sebelumnya tetap dipertahankan.
- Dashboard chart tetap memakai `MonitoringSummary` jika tersedia.
- Filter Dashboard memakai label `Tahun`, `Bulan SP2D / Bulan Fokus`, `Jenis SPM`, `Tampilkan`, dan `Reset`.
- `Tabel Dashboard INTERMILAN` memakai header Excel Dashboard:
  `Bulan SP2D`, `Cara Pembayaran`, `Nomor SPM`, `Jenis SPM`, `No. Kuitansi (Hanya untuk dana UP/PTUP)/No. SPM`, `No. DRPP`, `Uraian Belanja per Transaksi`, `Nilai (Bruto)`, `Pembebanan`, dan `% Kelengkapan`.
- Tabel Dashboard memakai preview D_K terbatas untuk mengikuti kolom Excel, tetapi tidak menggantikan alur D_K sebagai halaman transaksi lengkap.
- Monitoring memakai `MonitoringSummary` jika tersedia dan header ringkasannya mengikuti label panjang Excel.

### Batasan

Tidak ada perubahan menu/sidebar besar, role/permission, model, migration, database, PostgreSQL final, SQLite cleanup, OCR/parser, Upload Paket SPM penuh, atau Upload KW massal.

## 2026-07-06 — Reset PostgreSQL Testing Data Clean

### Konteks

Database PostgreSQL development sudah berisi hasil migrasi/import lama dari SQLite dan Excel. Pengguna meminta database aktif dibuat bersih untuk pengujian upload PDF/Excel dari awal tanpa menghapus user login dan data referensi.

### Keputusan

- Management command `reset_testing_data` ditambahkan dengan mode default dry-run dan opsi `--commit`.
- Saat `--commit`, command membuat backup JSON UTF-8 terlebih dahulu ke `backups/postgres/postgres_before_clean_reset_YYYYMMDD_HHMMSS.json`.
- Data transaksi/import yang dikosongkan: `SP2DImportBatch`, `SP2DRaw`, `TransactionDetail`, `DRPPUpload`, `DRPPItem`, `DRPPMatch`, `PaketSPMUpload`, `PaketSPMPreviewItem`, `DocumentUpload`, `DocumentDriveLink`, `ChecklistStatus`, dan `MonitoringSummary`.
- Data yang dipertahankan: `User`, `Profile`, `Group/Permission`, `MasterAkun`, dan `ChecklistTemplate`.
- Sequence PostgreSQL untuk model yang dibersihkan di-reset setelah cleanup.
- Halaman Review Data membaca count aktual database dan menampilkan empty state ketika database testing sudah bersih.
- Script bantu `scripts/reset_testing_data.ps1` ditambahkan untuk menjalankan command reset dan `check`.

### Batasan

Tidak ada perubahan model, migration, OCR/parser, Upload Paket SPM penuh, atau cleanup data referensi.

## 2026-07-06 — Sinkronisasi Folder Copy dan Parser Preview-First

### Konteks

Folder target VSCode adalah `C:\Users\muall\Documents\INTERMILAN PROJECT\intermilan_project`, sedangkan folder Copy menjadi sumber kebenaran terakhir. Setelah sinkronisasi, fitur upload/parser dilanjutkan tanpa mengubah baseline D_K, Monitoring, atau DocumentDriveLink.

### Keputusan

- Folder penting dari Copy disinkronkan ke target: `apps`, `templates`, `static`, `scripts`, `docs`, dan `intermilan_project/settings`.
- `.env`, `.venv`, `db.sqlite3`, `media/tmp`, `scratch`, cache, dan file runtime tidak disalin.
- Backup baseline `backups/postgres/postgres_baseline_ready_20260706_132244.json` tersedia di target.
- Parser SP2D Excel memakai deteksi header otomatis pada 30 baris awal dan menampilkan metadata preview.
- Parser PDF SPM/DRPP memakai text extraction opsional dan fallback OCR yang aman.
- Upload DRPP PDF dan Paket SPM PDF/ZIP memakai alur preview-first.
- Commit Paket SPM hanya membuat `PaketSPMUpload` dan `PaketSPMPreviewItem`, tidak mengubah D_K baseline.

### Batasan

Tidak ada model/migration baru. OCR scan penuh menunggu instalasi dependency opsional seperti Tesseract dan Poppler.

## 2026-07-07 — Multi-Engine OCR Preview-First

### Konteks

Pengguna meminta OCR diperkuat untuk SPM, DRPP, KW, dan Paket SPM tanpa reset database, tanpa cleanup data, tanpa migration baru, dan tanpa melanjutkan Upload Paket SPM penuh.

### Keputusan

- Helper OCR dipusatkan di `apps/core/ocr.py` dengan fungsi utama `extract_document_text(file_path, document_type=None)`.
- Urutan OCR default adalah `text,tesseract,paddleocr`.
- Engine text mencoba PyMuPDF, pdfplumber, dan pypdf/PyPDF2 bila tersedia.
- Tesseract memakai render PDF via PyMuPDF, preprocessing Pillow, konfigurasi `--psm 6`, dan fallback bahasa `ind+eng`, `ind`, lalu `eng`.
- PaddleOCR dan cloud OCR bersifat opsional serta dilewati aman jika dependency/env belum aktif.
- Preview DRPP dan Paket SPM menampilkan engine terbaik, status, confidence, warning, dan detail OCR dalam collapsible `Detail OCR`.
- Parser DRPP diperketat ke blok `BUKTI PENGELUARAN` dan berhenti sebelum area lampiran/COA agar item DRPP tidak tercampur lampiran.

### Batasan

Tidak ada perubahan model, migration, database, reset data, switch PostgreSQL final, OCR cloud aktif, atau Upload Paket SPM penuh.

## 2026-07-07 — Konsep Dua Sumber Data Keuangan

### Konteks

Pengguna meluruskan konsep final INTERMILAN: SP2D Excel adalah data awal dari rekap/laporan keuangan, sedangkan Paket SPM scan adalah sumber input alternatif yang dapat berdiri sendiri. Paket SPM tidak wajib menunggu SP2D. Jika keduanya ada, sistem melakukan matching/rekonsiliasi. Jika hanya salah satu ada, data tetap dapat masuk dan tampil.

### Keputusan

- Preview Paket SPM sekarang membedakan `Status Dokumen` dan `Status Rekonsiliasi`.
- Keputusan commit Paket SPM dihitung sebelum commit:
  - `Simpan dari Paket SPM` jika dokumen lengkap dan belum ada D_K/SP2D pembanding.
  - `Kaitkan Dokumen ke Data Existing` jika cocok dengan D_K/SP2D existing.
  - `Dokumen Sudah Ada` jika duplikat terdeteksi.
  - `Simpan Draft Review Manual` tetap nonaktif untuk OCR tidak yakin/GUP belum lengkap.
- D_K menampilkan label computed tanpa migration: `Sumber Data`, `No SP2D`, `Status Dokumen`, dan `Status Rekonsiliasi`.
- Baris Paket SPM dikenali lewat relasi existing `PaketSPMPreviewItem.matched_transaction`.
- Commit Paket SPM dapat membuat `TransactionDetail` dari hasil OCR jika tidak ada pembanding; jika ada D_K/SP2D existing, commit mengaitkan metadata dan dokumen ke data existing tanpa membuat baris duplikat.
- `/paket-spm/` menerima PDF SPM, DRPP, KW, dan ZIP; PDF tunggal diklasifikasi sebelum parser dijalankan.
- `/drpp/` menolak PDF yang terdeteksi sebagai SPM murni dan mengarahkan user ke menu Paket SPM.
- Cleanup Paket SPM diperkuat agar dapat menghapus data testing Paket SPM, preview item, D_K dari Paket SPM OCR, dan doclink terkait tanpa menyentuh baseline D_K/Monitoring/Document.

### Batasan

Tidak ada model baru, migration baru, reset database, restore SQLite, flush database, atau cleanup baseline. Field status sumber data/reconciliation masih computed dari relasi existing sampai desain model final disetujui.

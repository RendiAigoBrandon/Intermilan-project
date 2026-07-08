# 13 - Import Audit Report

## Ringkasan

- Waktu audit: 2026-07-01 08:48:55 WIB.
- Mode audit: read-only.
- Sumber audit utama: `docs/13_IMPORT_AUDIT_REPORT.md`.
- File timestamp atau rekap lain bukan sumber utama Tahap 2.2.
- Command:

```powershell
python manage.py audit_import_integrity
```

Command dijalankan dengan pembanding SQLite legacy dan folder Excel seed untuk audit sumber data serta warning tanggal.

## PostgreSQL Fresh Import Rehearsal 2026-07-02

Status persiapan rehearsal:

- Backup baseline: `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_before_postgresql_fresh_import_20260702`.
- Source ZIP: `C:\Users\muall\Documents\Magang BPS\Database awal.zip`.
- Folder extract: `C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal`.
- File Excel ditemukan: `INTERMILAN.xlsx` dan `KK_1300.xlsx` sampai `KK_1377.xlsx`.
- PostgreSQL lokal menerima koneksi pada `127.0.0.1:5432`.
- Pembuatan database `intermilan_rehearsal` dan role `intermilan_user` belum dijalankan otomatis karena koneksi `psql` sebagai `postgres` meminta password.
- Password PostgreSQL tidak disimpan di repo dan tidak ditulis ke dokumen.
- Command manual lanjutan dicatat di `docs/deployment.md`.

Validasi SQLite baseline tetap berhasil sebelum switch:

- `check`: berhasil.
- `test`: 13 test OK.
- `makemigrations --check --dry-run`: `No changes detected`.

Catatan: rehearsal PostgreSQL belum menghasilkan count PostgreSQL karena database/user belum dibuat pada sesi ini. SQLite development tetap menjadi database aktif.

## PostgreSQL Clean Baseline 2026-07-02

Keputusan terbaru: PostgreSQL rehearsal tidak lagi ditargetkan sama dengan SQLite development. PostgreSQL diarahkan menjadi clean baseline dari `Database awal.zip`.

Count PostgreSQL setelah import `Database awal.zip`:

- SP2DRaw: 0.
- D_K / TransactionDetail: 5.684.
- Master Akun: 42.
- ChecklistTemplate: 0.
- ChecklistStatus: 0.
- DocumentDriveLink: 3.081.
- DRPP Upload: 0.
- DRPP Item: 0.
- DRPP Match: 0.

Catatan selisih:

- SQLite development memiliki D_K 6.661, sedangkan PostgreSQL clean seed memiliki D_K 5.684.
- Selisih 977 D_K tidak disebabkan oleh checklist.
- D_K dan checklist adalah jenis data berbeda; checklist tidak boleh menambah/mengurangi jumlah D_K.
- Selisih 977 kemungkinan berasal dari data tambahan/test/legacy/manual pada SQLite development.
- File SP2D raw test, DRPP test, ChecklistStatus test, Upload Paket SPM test, dan eksperimen SQLite lama tidak dibawa ke clean baseline.
- SP2DRaw bernilai 0 pada PostgreSQL clean seed karena file SP2D raw akan dipakai untuk menguji fitur Upload & Inbox SP2D, bukan seed awal.

## Audit INTERMILAN.xlsx Dashboard dan Monitoring_Combine

Source:

- Workbook: `C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal\drive-download-20260615T061835Z-3-001\INTERMILAN.xlsx`.
- Sheet: `Dashboard` dan `Monitoring_Combine`.

Temuan `Dashboard`:

- Dimensi sheet: `A1:AA1002`.
- Baris non-empty: 23.
- Filter tahun berada pada area atas sheet dan contoh cached value adalah 2026.
- Filter bulan berada pada area atas sheet dan contoh cached value adalah `Januari`.
- Formula utama Dashboard memakai query ke `Monitoring_Combine!A:O`:
  `SELECT B,C,D,E,F,G,H,I,J,M,O WHERE C=<bulan> AND O=<tahun>`.
- Hasil query Dashboard untuk Januari 2026 menampilkan label satker seperti `bps1300`, `bps1301`, dan seterusnya.

Temuan `Monitoring_Combine`:

- Dimensi sheet: `A1:BD35290`.
- Baris ringkasan terisi: 480.
- Satker: 20.
- Bulan: Januari sampai Desember.
- Tahun: 2025 dan 2026.
- Distribusi: 40 baris per bulan karena 20 satker x 2 tahun.

Kolom penting `Monitoring_Combine`:

- `BPS Prov/Kab/Kota`
- `Bulan SP2D`
- `Realisasi FA 16 Detil Bulan ini (di isi satker)`
- `Realisasi Intermilan Bulan ini`
- `Realisasi Intermilan s.d Bulan Ini`
- `Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)`
- `Persentase Kelengkapan Dokumen`
- `Persentase SPJ yang sudah di Upload`
- `Persentase dokumen sudah di arsipkan`
- `Deadline`
- `Status`
- `% Completed`
- `BAR`
- `TA`

Mapping dataset chart Excel:

- Biru: `Realisasi FA 16 Detil Bulan ini (di isi satker)`.
- Merah: `Realisasi Intermilan Bulan ini`.
- Kuning: `Realisasi Intermilan s.d Bulan Ini`.
- Hijau: `Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)`.

Rekomendasi:

- Buat model `MonitoringSummary` untuk menyimpan seed resmi `Monitoring_Combine`.
- Dashboard web memakai `MonitoringSummary` bila tersedia.
- Fallback hitung dari D_K hanya digunakan jika `MonitoringSummary` belum diimport.

## Implementasi MonitoringSummary 2026-07-02

Status:

- Model `core.MonitoringSummary` sudah dibuat.
- Migration: `apps/core/migrations/0001_initial.py`.
- PostgreSQL rehearsal sudah menjalankan migration `core.0001_initial`.
- Command baseline: `import_monitoring_summary`.
- Command refresh: `refresh_monitoring_summary`.
- Import baseline PostgreSQL dari `INTERMILAN.xlsx -> Monitoring_Combine` berhasil:
  - read: 480.
  - success: 480.
  - skip: 0.
  - updated: 0.
  - failed: 0.
- Count `MonitoringSummary` PostgreSQL: 480.
- Source setelah import: `excel_seed` sebanyak 480.
- Tahun: 2025 = 240 row, 2026 = 240 row.
- Distinct satker: 20.

Catatan desain:

- `MonitoringSummary` bukan snapshot mati.
- Baseline awal berasal dari `Monitoring_Combine`.
- Setelah ada data harian baru, command refresh menghitung ulang nilai yang bisa berasal dari web.
- D_K mengubah `intermilan_bulan_ini` dan `intermilan_sd_bulan_ini`.
- FA16 tidak berubah saat D_K berubah.
- `persen_realisasi` dihitung dari Intermilan terhadap FA16 bila FA16 > 0.
- `last_refreshed_at` diisi saat refresh.
- `source` berubah menjadi `mixed` bila row menggabungkan FA16/baseline dan hasil kalkulasi web.

Validasi automated test:

- Import baseline membuat row `MonitoringSummary`.
- Refresh dari D_K mengubah Intermilan.
- Refresh tidak mengubah FA16.
- Persentase realisasi berubah saat D_K bertambah.
- Dashboard membaca `MonitoringSummary` setelah refresh.
- Refresh tidak membuat duplicate row.

## Validasi Route PostgreSQL 2026-07-02

Environment validasi:

- Backend: `django.db.backends.postgresql`.
- Database: `intermilan_rehearsal`.
- `MonitoringSummary`: 480 row.
- D_K / TransactionDetail: 5.684 row.
- Operator satker: 20 user.

User login yang tervalidasi:

- `admin`: berhasil.
- `operator_1300`: berhasil.
- `operator_1377`: berhasil.
- `viewer`: berhasil.

Route yang tervalidasi sebagai admin:

- `/`: 200.
- `/dashboard/?tahun=2026&bulan=6`: 200, memakai `MonitoringSummary`.
- `/sp2d/`: 200.
- `/dk/`: 200.
- `/documents/<transaction_id>/`: 200.
- `/monitoring/?tahun=2026&bulan=6&satker=1300`: 200, memakai `MonitoringSummary`.
- `/akun/`: 200.
- `/audit-data/`: 200.

Permission:

- `/audit-data/` untuk `viewer`: 403.
- `/audit-data/` untuk `operator_1300`: 403.
- Viewer melihat D_K sebagai read-only.

Sample Dashboard Juni 2026 dari `MonitoringSummary`:

- `bps1300`: FA16 `0`, Intermilan bulan ini `2.405.668.798`, persen realisasi `0,00%`.
- `bps1301`: FA16 `0`, Intermilan bulan ini `0`, persen realisasi `0,00%`.
- `bps1302`: FA16 `0`, Intermilan bulan ini `0`, persen realisasi `0,00%`.
- `bps1303`: FA16 `0`, Intermilan bulan ini `633.143.395`, persen realisasi `0,00%`.
- `bps1304`: FA16 `0`, Intermilan bulan ini `0`, persen realisasi `0,00%`.

Catatan validasi:

- `refresh_monitoring_summary --all` belum dijalankan pada PostgreSQL baseline agar angka Excel tetap utuh.
- Test suite dengan PostgreSQL tidak dijalankan penuh karena role `intermilan_user` tidak memiliki privilege `CREATEDB` untuk membuat database test. Test aplikasi tetap OK pada default test database.

## Jumlah Data

- SP2D batch: 43.
- SP2D raw: 1.874.
- D_K / TransactionDetail: 6.661.
- Master Akun: 53.
- Checklist template: 601.
- Checklist status: 127.188.
- DocumentDriveLink: 4.060.
- DRPP upload: 1.
- DRPP item: 4.
- DRPP match: 4.

## Temuan D_K / TransactionDetail

- Total Django: 6.661.
- Baris yang cocok dengan SQLite legacy: 5.359.
- Baris tambahan non-legacy, kemungkinan dari Excel/manual: 1.302.
- Baris tanpa nomor SPM/kuitansi/DRPP: 30.
- Grup kandidat duplikat berdasarkan satker, SPM, kuitansi, DRPP, akun, nilai bruto, dan nilai netto: 1.
- Sample kandidat duplikat:
  `1376 | SPM 00085A | KW 00085A | DRPP kosong | akun 522112 | bruto 37407.00 | netto 37407.00 -> 2 baris`.

Kesimpulan sementara: jumlah 6.661 belum boleh dianggap final bersih. Tambahan 1.302 perlu review, tetapi audit hanya melaporkan dan tidak menghapus data.

## Temuan Master Akun

- Total MasterAkun: 53.
- Legacy SQLite: 52 akun.
- Tambahan dibanding legacy: `51xxxx`.
- Kode kosong: 0.
- Kode invalid format: tidak ditemukan.
- Duplikat kode: tidak ditemukan.
- Kategori kosong: tidak ditemukan pada sample audit.

Kesimpulan sementara: tambahan 1 akun berasal dari kode `51xxxx` dan tidak terlihat sebagai duplikat kode.

## Temuan ChecklistTemplate

- Total ChecklistTemplate Django: 601.
- Total checklist_template di SQLite legacy: 601.
- Distinct `nama_dokumen`: 168.
- Duplikat exact `nama_dokumen+kategori`: 0.
- Nama dokumen berulang lintas kategori/pattern, antara lain:
  - SP2D: 38 template.
  - SPM: 38 template.
  - DRPP: 33 template.
  - KAK: 32 template.
  - SPBy: 32 template.
  - Form permintaan/ nota dinas: 30 template.
  - Realisasi BOS: 30 template.

Kesimpulan sementara: angka 601 berasal dari tabel template legacy yang menyimpan kombinasi pattern/kategori, bukan hasil salah import dari ChecklistStatus. Untuk master UI, data ideal kemungkinan perlu dinormalisasi menjadi distinct dokumen atau dipisah antara master dokumen dan rule/pattern.

## Temuan ChecklistStatus

- Total ChecklistStatus: 127.188.
- Total TransactionDetail: 6.661.
- Rata-rata checklist per transaksi: 19,09.
- Orphan transaction_detail: 0.
- Duplikat `transaction_detail + nama_dokumen`: 0.
- Status:
  - ADA: 9.082.
  - BELUM: 116.472.
  - TIDAK_PERLU: 1.634.

Kesimpulan sementara: relasi ChecklistStatus valid, tidak ditemukan orphan atau duplikat pasangan transaksi-dokumen.

## Temuan DocumentDriveLink

- Total DocumentDriveLink: 4.060.
- Matched ke TransactionDetail: 3.994.
- Belum matched: 66.
- Link kosong: 0.
- Nama file/key kosong: 0.
- Invalid Google Drive/Docs URL sample: 1.
- Sample invalid: `id=813 satker=1303 spm=00040A url=00040A.pdf`.
- Duplikat URL: 0 grup.

Kesimpulan sementara: mayoritas link sudah match ke transaksi, tetapi 66 link perlu review manual dan 1 URL terlihat berupa nama file lokal, bukan link Google Drive.

## Temuan SP2D

- Total SP2DRaw: 1.874.
- Duplikat non-empty `no_sp2d`: 90 grup.
- `no_sp2d` kosong: 1.694.
- `nomor_spm_extracted` kosong: 0.
- `nilai_sp2d <= 0`: 126.
- `tanggal_sp2d` null: 96.
- Satker kosong: 0.

Kesimpulan sementara: banyak baris SP2D memakai nomor SPM extracted sebagai kunci kerja, sementara `no_sp2d` belum lengkap pada sebagian besar baris. Duplikat `no_sp2d` perlu review sebelum dipakai sebagai unique key.

## Warning Tanggal Excel

Warning tanggal ditemukan pada:

- Workbook: `KK_1308.xlsx`.
- Sheet: `D_K`.
- Cell: `F256-F280`.
- Kolom: Tanggal SPM.
- Nilai serial:
  - `6693561.0` untuk `F256-F275`.
  - `6693566.0` untuk `F276-F280`.
- Dampak: openpyxl menganggap serial tanggal di luar batas. Import memperlakukan tanggal bermasalah sebagai kosong/null dan baris tetap diproses.

## Audit Data Dummy

Potensi keyword ditemukan:

- `TransactionDetail.deskripsi` mengandung `test`: 2 baris.
- `SP2DRaw.deskripsi` mengandung `test`: 1 baris.
- Keyword `dummy`, `sample`, `contoh` tidak menjadi temuan utama pada ringkasan audit.
- SPM dengan karakter tidak masuk akal: tidak ditemukan pada sample audit.

Temuan ini perlu review manual karena kata `test` bisa saja bagian dari deskripsi asli.

## Rekomendasi

1. Jangan hapus atau replace data sebelum pengguna menyetujui rencana cleanup.
2. Review 1.302 D_K tambahan dan 1 grup kandidat duplikat.
3. Review 66 DocumentDriveLink belum match dan 1 URL invalid.
4. Review 25 cell tanggal bermasalah di `KK_1308.xlsx` sebelum data tanggal dipakai untuk laporan resmi.
5. Pertimbangkan migration masa depan untuk field `source` di `TransactionDetail`, tetapi jangan lakukan sebelum disetujui.
6. Pertimbangkan normalisasi `ChecklistTemplate` menjadi master dokumen + rule/pattern terpisah, tetapi jangan lakukan sebelum disetujui.

## Tahap 2.2 - Review Audit Data

Halaman read-only ditambahkan untuk review temuan audit:

- Route: `/audit-data/`.
- Export CSV: `/audit-data/export/`.
- Menu: `Review Data`.
- Tombol cleanup/merge/fix otomatis tidak disediakan.
- Tombol disabled `Belum Aktif - Menunggu Persetujuan` dipakai sebagai penanda bahwa perbaikan data belum boleh dieksekusi.
- Export CSV menggunakan filename `audit_data_intermilan_YYYYMMDD.csv`.
- Kolom CSV: `kategori`, `item`, `jumlah`, `detail`, `status_review`, `rekomendasi`.

Data yang ditampilkan:

- D_K tambahan non-legacy: 1.302.
- Kandidat duplikat D_K: 1 grup.
- Master Akun tambahan: `51xxxx`.
- ChecklistTemplate: 601.
- Distinct nama dokumen: 168.
- DocumentDriveLink belum matched: 66.
- URL Google Drive invalid sample: `00040A.pdf`.
- SP2D `no_sp2d` kosong: 1.694.
- Duplikat `no_sp2d`: 90 grup.
- Tanggal Excel invalid: `KK_1308.xlsx`, sheet `D_K`, cell `F256-F280`.
- Keyword `test`: `TransactionDetail.deskripsi` 2 baris dan `SP2DRaw.deskripsi` 1 baris.

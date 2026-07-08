# 02 - Database Schema

## Tahap 2 Foundation

Database Django sekarang memuat model foundation untuk data asli INTERMILAN:

- `accounts.Profile`: role, satker, dan flag perubahan password.
- `sp2d.SP2DImportBatch`: metadata import SP2D.
- `sp2d.SP2DRaw`: data mentah SP2D.
- `dk.TransactionDetail`: data D_K/detail keuangan.
- `dk.MasterAkun`: master akun untuk kategori dan pisah per akun.
- `documents.ChecklistTemplate`: template dokumen wajib/opsional.
- `documents.ChecklistStatus`: status checklist per transaksi.
- `documents.DocumentUpload`: metadata file lokal.
- `documents.DocumentDriveLink`: metadata link Google Drive.
- `drpp.DRPPUpload`, `drpp.DRPPItem`, `drpp.DRPPMatch`: foundation DRPP.
- `auditlog.AuditLog`: catatan aktivitas.

## Model Baru Tahap 2

### MasterAkun

Field utama:

- `kode`
- `nama_akun`
- `kategori`
- `is_active`
- `source`

Index:

- `kategori`
- `is_active`

### DocumentDriveLink

Field utama:

- `transaction_detail`
- `satker_code`
- `nomor_spm`
- `no_kuitansi`
- `no_drpp`
- `jenis_dokumen`
- `nama_file`
- `google_drive_url`
- `status`
- `catatan`
- `created_by`
- `created_at`
- `updated_at`

Index:

- `satker_code`, `nomor_spm`
- `no_kuitansi`
- `no_drpp`
- `jenis_dokumen`
- `status`

## Production Database Readiness

Settings mendukung:

- SQLite untuk development.
- PostgreSQL sebagai rekomendasi production.
- MySQL sebagai opsi jika driver tersedia.

Konfigurasi menggunakan environment:

- `DATABASE_ENGINE`
- `DATABASE_NAME`
- `DATABASE_USER`
- `DATABASE_PASSWORD`
- `DATABASE_HOST`
- `DATABASE_PORT`
- `DATABASE_URL`

## Tahap 2.1 - Audit Integritas Import

Command read-only disiapkan untuk memeriksa hasil import tanpa mengubah database:

```powershell
python manage.py audit_import_integrity
```

Command ini memeriksa:

- jumlah data per model utama;
- kecocokan D_K/TransactionDetail terhadap SQLite legacy;
- kandidat duplikat berdasarkan satker, SPM, kuitansi, DRPP, akun, nilai bruto, dan nilai netto;
- kualitas MasterAkun;
- struktur ChecklistTemplate dan ChecklistStatus;
- validitas dan kecocokan DocumentDriveLink;
- kualitas SP2DRaw;
- warning tanggal Excel;
- potensi data dummy/sample/test.

Catatan struktur:

- `TransactionDetail` belum memiliki field `source`, sehingga pemisahan legacy vs tambahan Excel pada audit memakai pembanding ID legacy dari SQLite.
- `ChecklistTemplate` saat ini mengikuti isi tabel legacy, yaitu kombinasi template/pattern/kategori. Untuk master UI, data ini kemungkinan perlu dinormalisasi pada tahap perbaikan terpisah setelah disetujui.
- `DocumentDriveLink.transaction_detail` dipakai sebagai relasi hasil match ke D_K. Link yang belum match tetap dipertahankan sebagai metadata dan tidak dihapus otomatis.

## Reset Testing Data PostgreSQL

Command berikut dipakai untuk mengosongkan data transaksi/import lama pada database testing tanpa menghapus referensi dasar:

```powershell
python manage.py reset_testing_data --dry-run
python manage.py reset_testing_data --commit
```

Model yang dikosongkan:

- `SP2DImportBatch`
- `SP2DRaw`
- `TransactionDetail`
- `DRPPUpload`, `DRPPItem`, `DRPPMatch`
- `PaketSPMUpload`, `PaketSPMPreviewItem`
- `DocumentUpload`, `DocumentDriveLink`
- `ChecklistStatus`
- `MonitoringSummary`

Model yang dipertahankan:

- `User`
- `Profile`
- `Group` dan permission bawaan Django
- `MasterAkun`
- `ChecklistTemplate`

Saat `--commit`, command membuat backup JSON ke `backups/postgres/` sebelum cleanup dan mereset sequence PostgreSQL untuk model yang dikosongkan.

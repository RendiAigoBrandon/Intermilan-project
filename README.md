# INTERMILAN Django

Rebuild Tahap 1 aplikasi INTERMILAN untuk kebutuhan internal BPS Provinsi Sumatera Barat. Project Flask lama hanya dipakai sebagai referensi fitur, struktur data, UI flow, dan migrasi data read-only.

## Instalasi

```powershell
cd "C:\Users\muall\Documents\INTERMILAN PROJECT\intermilan_project"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`, terutama `SECRET_KEY`, `DEBUG`, dan `ALLOWED_HOSTS`.

## Database dan User Awal

```powershell
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Buka `http://127.0.0.1:8000/`.

## PostgreSQL Rehearsal

PostgreSQL rehearsal dapat dijalankan dengan environment variable sementara tanpa menghapus SQLite development. Password asli PostgreSQL tidak boleh ditulis ke repo, README, docs, atau `.env.example`.

Panduan rehearsal ada di `docs/deployment.md`.

## Cara Running Lokal dengan PostgreSQL

Project lokal sekarang membaca `.env` otomatis lewat `python-dotenv`. File `.env` tidak masuk git dan berisi konfigurasi PostgreSQL lokal.

Sekali setup/migrasi dari SQLite ke PostgreSQL:

```powershell
.\scripts\setup_postgres_from_sqlite.ps1
```

Running harian cukup:

```powershell
.\scripts\run_dev_postgres.ps1
```

Script harian akan:

- masuk ke root project,
- membaca `.env`,
- mengaktifkan `.venv` kalau ada,
- mencoba memastikan service PostgreSQL berjalan,
- menjalankan `python manage.py check`,
- menjalankan `python manage.py runserver`.

Jika service PostgreSQL gagal dinyalakan karena hak akses Windows, buka PowerShell sebagai Administrator atau aktifkan service PostgreSQL secara manual dari Services.

Untuk membuat user development seluruh satker aktif setelah `MonitoringSummary`/D_K tersedia:

```powershell
python manage.py create_dev_users --password "bps12345" --all-satker
```

## Struktur Aplikasi

- `apps/accounts`: login, profile, role, akses satker.
- `apps/core`: dashboard, home internal, layout utama.
- `apps/sp2d`: batch import dan data mentah SP2D.
- `apps/dk`: data D_K / `TransactionDetail`.
- `apps/documents`: upload dokumen dan checklist.
- `apps/drpp`: upload DRPP, item DRPP, dan matching.
- `apps/paket_spm`: kerangka awal Upload Paket SPM.
- `apps/reports`: kerangka laporan/export.
- `apps/auditlog`: log aktivitas user.

## UI dan Font

- UI memakai font lokal Inter dari `static/fonts/inter/`.
- Font dipanggil lewat `@font-face` di `static/css/base.css` dengan fallback Arial/Helvetica.
- D_K memakai server-side pagination; gunakan query `page`, `page_size=20|50|100`, dan filter GET seperti `q`, `satker`, `bulan`, atau `akun`.

## Migrasi Legacy

Script awal ada di `scripts/import_legacy_sqlite.py`. Script ini dirancang membaca database lama `instance/sp2d_kk1300.sqlite` secara read-only dan memasukkan data ke model Django. Tahap 1 belum menjalankan migrasi penuh.

```powershell
python scripts/import_legacy_sqlite.py --legacy-db "path\to\sp2d_kk1300.sqlite" --dry-run
```

## Catatan Tahap 1

- Parser PDF, OCR, Upload Paket SPM penuh, dan Upload KW massal belum diimplementasikan.
- File Flask lama tidak diperbaiki, tidak dijadikan base, dan database lama tidak diubah.
- Semua halaman internal memakai login Django dan CSRF bawaan.

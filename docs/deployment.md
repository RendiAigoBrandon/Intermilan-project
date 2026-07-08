# Deployment

## Development

Gunakan SQLite lokal dan settings:

```powershell
set DJANGO_SETTINGS_MODULE=intermilan_project.settings.development
python manage.py runserver
```

## Production

Gunakan settings production dan PostgreSQL. Tahap 2.2 hanya menyiapkan readiness; database aktif development belum dipindahkan ke PostgreSQL.

```powershell
set DJANGO_SETTINGS_MODULE=intermilan_project.settings.production
set DJANGO_SECRET_KEY=<secret-kuat>
set DJANGO_DEBUG=False
set DJANGO_ALLOWED_HOSTS=intermilan.example.go.id
set DATABASE_ENGINE=postgresql
set DATABASE_NAME=intermilan_db
set DATABASE_USER=intermilan_user
set DATABASE_PASSWORD=<password-kuat>
set DATABASE_HOST=127.0.0.1
set DATABASE_PORT=5432
python manage.py migrate
python manage.py collectstatic
```

Alternatif single URL:

```powershell
set DATABASE_URL=postgres://intermilan_user:<password-kuat>@127.0.0.1:5432/intermilan_db
```

## Setup PostgreSQL Nanti

1. Install PostgreSQL.
2. Buat database, misalnya `intermilan_db`.
3. Buat user, misalnya `intermilan_user`.
4. Beri hak akses user ke database `intermilan_db`.
5. Salin `.env.example` menjadi `.env` dan isi variabel `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DATABASE_ENGINE`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST`, `DATABASE_PORT`, `MEDIA_ROOT`, dan `STATIC_ROOT`.
6. Jalankan migration hanya saat switch sudah disetujui.
7. Import ulang data ke PostgreSQL hanya jika sudah disetujui.

Contoh SQL awal:

```sql
CREATE DATABASE intermilan_db;
CREATE USER intermilan_user WITH PASSWORD 'ganti-password-kuat';
GRANT ALL PRIVILEGES ON DATABASE intermilan_db TO intermilan_user;
```

Catatan penting:

- Jangan hapus SQLite development saat readiness.
- Jangan switch database aktif sebelum ada persetujuan.
- Jangan import ulang data ke PostgreSQL sebelum ada persetujuan.

Pastikan aplikasi dijalankan di belakang reverse proxy HTTPS, backup database aktif, dan akses media upload dibatasi sesuai kebijakan internal.

## PostgreSQL Fresh Import Rehearsal 2026-07-02

Status rehearsal:

- Backup baseline sudah dibuat sebelum rehearsal:
  `C:\Users\muall\Documents\INTERMILAN PROJECT\backups\intermilan_project_before_postgresql_fresh_import_20260702`
- Source ZIP sudah diekstrak ke:
  `C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal`
- File yang ditemukan: `INTERMILAN.xlsx` dan `KK_1300.xlsx` sampai `KK_1377.xlsx`.
- PostgreSQL service menerima koneksi pada `127.0.0.1:5432`.
- Pembuatan database/user belum dijalankan otomatis karena `psql` meminta password superuser `postgres` dan password tidak boleh disimpan atau dikirim lewat chat.
- SQLite development belum dihapus dan belum diganti.

Jalankan bagian ini secara manual di terminal lokal agar password tidak tersimpan di repo.

### 1. Buat Database dan User

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

Di prompt `psql`, jalankan:

```sql
SELECT 'CREATE DATABASE intermilan_rehearsal'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'intermilan_rehearsal')\gexec

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'intermilan_user') THEN
        CREATE ROLE intermilan_user LOGIN PASSWORD '<ISI_PASSWORD_DI_TERMINAL>';
    ELSE
        ALTER ROLE intermilan_user WITH LOGIN PASSWORD '<ISI_PASSWORD_DI_TERMINAL>';
    END IF;
END
$$;

GRANT ALL PRIVILEGES ON DATABASE intermilan_rehearsal TO intermilan_user;
\c intermilan_rehearsal
GRANT ALL ON SCHEMA public TO intermilan_user;
ALTER SCHEMA public OWNER TO intermilan_user;
\q
```

Jika database/user sudah ada, cek dengan:

```sql
\l
\du
```

### 2. Jalankan Django dengan Environment PostgreSQL Sementara

Gunakan environment PowerShell sementara. Jangan tulis password asli ke `.env`, README, atau dokumentasi.

```powershell
$env:DJANGO_SETTINGS_MODULE="intermilan_project.settings.development"
$env:DATABASE_ENGINE="postgresql"
$env:DATABASE_NAME="intermilan_rehearsal"
$env:DATABASE_USER="intermilan_user"
$env:DATABASE_PASSWORD="<ISI_PASSWORD_DI_TERMINAL>"
$env:DATABASE_HOST="127.0.0.1"
$env:DATABASE_PORT="5432"
```

### 3. Migration dan Import

```powershell
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" migrate
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" import_excel_seed --path "C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal" --commit
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" import_monitoring_summary --path "C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal" --commit
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" create_dev_users --password "bps12345" --all-satker
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" check
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" test
```

Catatan seed:

- Seed rehearsal memakai `Database awal.zip`.
- File SP2D raw test seperti `Daftar SP2D Satker (Detail).xlsx` tidak dimasukkan ke seed awal.
- Selisih jumlah `SP2DRaw` terhadap SQLite development dapat wajar jika SQLite sebelumnya sudah memuat SP2D raw test atau sumber legacy tambahan.
- PostgreSQL rehearsal diarahkan sebagai clean baseline dari `Database awal.zip`, bukan salinan penuh SQLite development.
- Count PostgreSQL tidak wajib sama dengan SQLite development karena SQLite dapat berisi data test/legacy/manual.
- Selisih D_K tidak boleh dikaitkan dengan checklist; D_K dan checklist adalah data berbeda.
- Dashboard Excel lama memakai `INTERMILAN.xlsx` sheet `Monitoring_Combine`; ringkasan ini diimport dengan `import_monitoring_summary` sebagai seed `MonitoringSummary`.
- Setelah data web baru masuk, jalankan `refresh_monitoring_summary` agar Dashboard/Monitoring mengikuti data terbaru tanpa mengubah FA16 dari D_K.
- `create_dev_users --all-satker` membuat operator untuk satker aktif dari `MonitoringSummary`/D_K, bukan range kode hardcoded.
- Jika menjalankan test suite langsung pada PostgreSQL, role database perlu privilege `CREATEDB` untuk membuat database test. Jika tidak, validasi route dapat dilakukan pada database rehearsal dan test aplikasi tetap dijalankan pada default test database.

### 4. Setelah Rehearsal

Unset environment agar shell kembali ke default SQLite:

```powershell
Remove-Item Env:DATABASE_ENGINE,Env:DATABASE_NAME,Env:DATABASE_USER,Env:DATABASE_PASSWORD,Env:DATABASE_HOST,Env:DATABASE_PORT -ErrorAction SilentlyContinue
```

Switch final ke PostgreSQL hanya boleh dilakukan setelah count, route, login, dan import dinyatakan aman.

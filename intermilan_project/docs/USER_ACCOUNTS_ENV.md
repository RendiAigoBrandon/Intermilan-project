# User Account Management - Environment Variables

## Overview

User accounts (admin dan satker) dikelola melalui Environment Variables di Coolify. Password tidak lagi hardcoded di source code.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    COOLIFY ENV VARS                          │
│  ADMIN_USERNAME, ADMIN_PASSWORD                              │
│  SATKER_1300_USERNAME, SATKER_1300_PASSWORD                  │
│  ... (setiap satker punya credentials masing-masing)         │
└─────────────────────────────────────────────────────────────┘
                              ↓ read
┌─────────────────────────────────────────────────────────────┐
│            seed_production_users.py                          │
│  - Baca dari os.environ                                      │
│  - set_password() → hash → simpan DB                       │
│  - Idempotent: skip jika sudah ada (default)                │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    DATABASE (auth_user)                      │
│  - Password tersimpan sebagai hash Django (PBKDF2)          │
│  - Plain text TIDAK pernah disimpan                         │
└─────────────────────────────────────────────────────────────┘
```

## Environment Variables (Coolify)

### Admin Account

| Variable | Description | Example |
|----------|-------------|---------|
| `ADMIN_USERNAME` | Username admin pusat | `admin` |
| `ADMIN_PASSWORD` | Password admin | `PasswordKuat123!` |

### SATKER Accounts (21 units)

Setiap satker memiliki 2 environment variable:

| Variable | Description | Example |
|----------|-------------|---------|
| `SATKER_XXXX_USERNAME` | Username satker | `KK_1300` |
| `SATKER_XXXX_PASSWORD` | Password satker | `PasswordSatker1300!` |

#### Daftar SATKER Codes

| Code | Nama | Username | Password Variable |
|------|------|----------|-------------------|
| 1300 | BPS Provinsi Sumatera Barat | `KK_1300` | `SATKER_1300_PASSWORD` |
| 1301 | BPS Kabupaten Kepulauan Mentawai | `KK_1301` | `SATKER_1301_PASSWORD` |
| 1302 | BPS Kabupaten Pesisir Selatan | `KK_1302` | `SATKER_1302_PASSWORD` |
| 1303 | BPS Kabupaten Solok | `KK_1303` | `SATKER_1303_PASSWORD` |
| 1304 | BPS Kabupaten Sijunjung | `KK_1304` | `SATKER_1304_PASSWORD` |
| 1305 | BPS Kabupaten Tanah Datar | `KK_1305` | `SATKER_1305_PASSWORD` |
| 1306 | BPS Kabupaten Padang Pariaman | `KK_1306` | `SATKER_1306_PASSWORD` |
| 1307 | BPS Kabupaten Agam | `KK_1307` | `SATKER_1307_PASSWORD` |
| 1308 | BPS Kabupaten Lima Puluh Kota | `KK_1308` | `SATKER_1308_PASSWORD` |
| 1309 | BPS Kabupaten Pasaman | `KK_1309` | `SATKER_1309_PASSWORD` |
| 1310 | BPS Kabupaten Solok Selatan | `KK_1310` | `SATKER_1310_PASSWORD` |
| 1311 | BPS Kabupaten Dharmasraya | `KK_1311` | `SATKER_1311_PASSWORD` |
| 1312 | BPS Kabupaten Pasaman Barat | `KK_1312` | `SATKER_1312_PASSWORD` |
| 1371 | BPS Kota Padang | `KK_1371` | `SATKER_1371_PASSWORD` |
| 1372 | BPS Kota Solok | `KK_1372` | `SATKER_1372_PASSWORD` |
| 1373 | BPS Kota Sawahlunto | `KK_1373` | `SATKER_1373_PASSWORD` |
| 1374 | BPS Kota Padang Panjang | `KK_1374` | `SATKER_1374_PASSWORD` |
| 1375 | BPS Kota Bukittinggi | `KK_1375` | `SATKER_1375_PASSWORD` |
| 1376 | BPS Kota Payakumbuh | `KK_1376` | `SATKER_1376_PASSWORD` |
| 1377 | BPS Kota Pariaman | `KK_1377` | `SATKER_1377_PASSWORD` |

## Command Usage

### 1. Initial Setup (Create Users)

Jalankan SEKALI saat pertama kali deploy untuk membuat user:

```bash
python manage.py seed_production_users
```

**Perilaku:**
- Membuat user baru jika belum ada
- Skip user existing (password TIDAK diubah)
- Cocok untuk initial setup

### 2. Preview Changes

```bash
python manage.py seed_production_users --dry-run
```

**Perilaku:**
- Menampilkan user yang akan dibuat/diubah
- Tidak ada perubahan di database

### 3. Sync Passwords (After Changing ENV)

Jika Anda mengubah password di Coolify Environment Variables:

```bash
python manage.py seed_production_users --sync-passwords
```

**Perilaku:**
- Membaca password dari Environment Variables
- Mengganti password user sesuai ENV
- Menggunakan `set_password()` (hashed)

### 4. Update Profile Only (No Password Change)

Jika Anda ingin update role/profile tanpa mengubah password:

```bash
python manage.py seed_production_users --force
```

**Perilaku:**
- Update profile user existing
- Password TIDAK diubah

## Workflow: Ganti Password

### Admin Password

1. Buka Coolify → Environment Variables
2. Ubah `ADMIN_PASSWORD`
3. Redeploy container
4. Jalankan: `python manage.py seed_production_users --sync-passwords`
5. User admin bisa login dengan password baru

### SATKER Password

1. Buka Coolify → Environment Variables
2. Ubah `SATKER_XXXX_PASSWORD` (sesuai satker)
3. Redeploy container
4. Jalankan: `python manage.py seed_production_users --sync-passwords`
5. User satker bisa login dengan password baru

## Security

- ✅ Password di-hash menggunakan Django's PBKDF2
- ✅ Plain text TIDAK pernah disimpan di database
- ✅ Password TIDAK hardcoded di source code
- ✅ Password hanya ada di Coolify Environment Variables
- ✅ Tidak ada password di git repository

## Template Coolify Environment Variables

Salin template ini ke Coolify:

```
ADMIN_USERNAME=admin
ADMIN_PASSWORD=GantiDenganPasswordKuat1!

SATKER_1300_USERNAME=KK_1300
SATKER_1300_PASSWORD=GantiDenganPasswordKuat2!
SATKER_1301_USERNAME=KK_1301
SATKER_1301_PASSWORD=GantiDenganPasswordKuat3!
SATKER_1302_USERNAME=KK_1302
SATKER_1302_PASSWORD=GantiDenganPasswordKuat4!
SATKER_1303_USERNAME=KK_1303
SATKER_1303_PASSWORD=GantiDenganPasswordKuat5!
SATKER_1304_USERNAME=KK_1304
SATKER_1304_PASSWORD=GantiDenganPasswordKuat6!
SATKER_1305_USERNAME=KK_1305
SATKER_1305_PASSWORD=GantiDenganPasswordKuat7!
SATKER_1306_USERNAME=KK_1306
SATKER_1306_PASSWORD=GantiDenganPasswordKuat8!
SATKER_1307_USERNAME=KK_1307
SATKER_1307_PASSWORD=GantiDenganPasswordKuat9!
SATKER_1308_USERNAME=KK_1308
SATKER_1308_PASSWORD=GantiDenganPasswordKuat10!
SATKER_1309_USERNAME=KK_1309
SATKER_1309_PASSWORD=GantiDenganPasswordKuat11!
SATKER_1310_USERNAME=KK_1310
SATKER_1310_PASSWORD=GantiDenganPasswordKuat12!
SATKER_1311_USERNAME=KK_1311
SATKER_1311_PASSWORD=GantiDenganPasswordKuat13!
SATKER_1312_USERNAME=KK_1312
SATKER_1312_PASSWORD=GantiDenganPasswordKuat14!
SATKER_1371_USERNAME=KK_1371
SATKER_1371_PASSWORD=GantiDenganPasswordKuat15!
SATKER_1372_USERNAME=KK_1372
SATKER_1372_PASSWORD=GantiDenganPasswordKuat16!
SATKER_1373_USERNAME=KK_1373
SATKER_1373_PASSWORD=GantiDenganPasswordKuat17!
SATKER_1374_USERNAME=KK_1374
SATKER_1374_PASSWORD=GantiDenganPasswordKuat18!
SATKER_1375_USERNAME=KK_1375
SATKER_1375_PASSWORD=GantiDenganPasswordKuat19!
SATKER_1376_USERNAME=KK_1376
SATKER_1376_PASSWORD=GantiDenganPasswordKuat20!
SATKER_1377_USERNAME=KK_1377
SATKER_1377_PASSWORD=GantiDenganPasswordKuat21!
```

## Troubleshooting

### "WARNING: ADMIN_USERNAME or ADMIN_PASSWORD not set"

Pastikan environment variable sudah diset di Coolify dan container sudah di-redeploy.

### "WARNING: Missing Environment Variables for SATKER"

SATKER tertentu belum dikonfigurasi. Anda bisa:
1. Set semua SATKER environment variables
2. Atau abaikan jika tidak semua satker diperlukan

### User tidak bisa login setelah sync password

1. Pastikan password di ENV sudah benar
2. Pastikan container sudah di-redeploy
3. Jalankan command dengan `--dry-run` dulu untuk cek
4. Pastikan tidak ada typo di environment variable name

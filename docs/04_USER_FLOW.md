# 04 - User Flow

## Admin

1. Login.
2. Buka Dashboard untuk melihat total SP2D, D_K, DRPP, dan dokumen.
3. Jalankan import data melalui management command.
4. Cek SP2D, D_K, Monitoring, Master Akun, dan Akun Keuangan.
5. Mengelola semua satker.
6. Mengakses Review Data dan export audit.

## Operator Satker

1. Login.
2. Melihat dan mengedit data satker sendiri.
3. Melihat monitoring lintas satker secara read-only.
4. Mengelola checklist dan dokumen hanya untuk satkernya.
5. Tidak dapat mengakses Review Data atau import global.

## Viewer

1. Login.
2. Melihat monitoring/read-only.
3. Tidak memiliki akses edit/upload.
4. Tombol upload/edit/export ditampilkan sebagai read-only atau disembunyikan.

## Role Access Tahap 2.3

- Admin dapat melihat dan mengelola semua satker.
- Operator Satker dapat mengedit/upload data satker sendiri dan melihat monitoring lintas satker sebagai read-only.
- Viewer hanya baca dan tidak dapat upload, edit, delete, import, cleanup, atau fix otomatis.
- Backend tetap memvalidasi permission; UI hanya membantu menyembunyikan/disable tombol.

## Import Data Tahap 2

1. Jalankan dry-run SQLite.
2. Jalankan import SQLite dengan `--commit`.
3. Jalankan dry-run Excel.
4. Jalankan import Excel dengan `--commit` bila diperlukan untuk melengkapi data.
5. Cek halaman data.

## Alur Checklist Dokumen & DRPP

1. User membuka halaman Detail Keuangan / D_K.
2. Setiap baris transaksi menampilkan tombol `Checklist` atau `Lihat Checklist`.
3. User memilih tombol tersebut pada transaksi yang diinginkan.
4. Sistem membuka detail di `/documents/<transaction_id>/`.
5. Jika transaksi membutuhkan DRPP atau sudah punya data DRPP, D_K menampilkan tombol `Upload DRPP` atau `Lihat DRPP`.
6. Admin dapat membuka semua transaksi; Operator Satker hanya dapat edit/upload untuk satker sendiri; Viewer hanya read-only.

Catatan: `/documents/` tidak menjadi daftar transaksi duplikat D_K dan tidak boleh membuka transaksi default seperti SPM `00074T`.

## Perbedaan Status Belum Lengkap

- Inbox SP2D belum lengkap berada di `/sp2d/`: SP2D raw belum punya detail akun, belum cocok dengan D_K, atau masih perlu diproses.
- Dokumen transaksi belum lengkap berada pada konteks D_K/Monitoring: transaksi D_K sudah ada, tetapi dokumen pendukung seperti SPM, kuitansi, DRPP, SSP, link Google Drive, atau item checklist lain belum lengkap.
- Detail checklist dokumen hanya dibuka dari tombol Checklist pada baris D_K.

## Alur Sheet Ke Web

- Daftar SP2D / Inbox SP2D diterjemahkan sebagai data mentah SP2D di `/sp2d/`.
- D_K diterjemahkan sebagai detail transaksi keuangan lengkap di `/dk/`.
- Ringkasan Excel diterjemahkan ke Dashboard dan Monitoring, bukan menjadi tabel input transaksi baru.
- Status Detail pada Inbox SP2D menunjukkan apakah baris SP2D sudah memiliki detail D_K.

## Dashboard MoM

- Dashboard menampilkan scope data aktif sesuai role: Admin semua satker, Operator Satker hanya satker sendiri, Viewer semua satker secara read-only.
- Filter Dashboard memakai query parameter GET `tahun` dan `bulan`; tombol Reset menghapus query.
- Dropdown bulan Dashboard selalu menampilkan Januari-Desember.
- Chart utama Dashboard membandingkan data antar satker pada bulan terpilih, dengan X-axis `bps1300`, `bps1301`, dan seterusnya.
- Operator Satker tetap melihat chart monitoring lintas satker secara read-only, tetapi tabel/kartu operasional tetap mengikuti scope satker sendiri.
- Data aktif import awal masih berada pada Januari-Juli 2026; bulan tanpa data tetap tampil 0 pada filter bulan terkait.


# Instruksi Proyek INTERMILAN

Instruksi ini berlaku untuk seluruh repository.

## Sumber Aturan Utama

Sebelum mengubah OCR, parser, Upload Paket SPM, SPM, SPP, SP2D, DRPP, KW/Bukti, Lampiran COA, atau Database D_K, wajib membaca:

`docs/PARSER_PAKET_SPM_SPEC.md`

Dokumen tersebut adalah sumber aturan utama. Jangan mengubah spesifikasi tanpa permintaan pengguna.

## Aturan Wajib

- Dilarang membuat patch khusus berdasarkan filename, nomor dokumen, nominal, deskripsi, nomor halaman, atau satu sampel PDF.
- Regression corpus hanya untuk test, bukan hardcode production.
- Gunakan parser tabel berbasis cell/koordinat dan klasifikasi per halaman.
- Jangan memakai gabungan flat text seluruh PDF untuk membuat transaksi.
- Jika struktur atau nilai tidak yakin, hasil wajib Perlu Review dan tidak boleh ditebak.
- Data salah tidak boleh dianggap valid atau mengubah D_K.
- D_K existing adalah sumber utama dan 15 kolom existing tidak boleh dibangun ulang atau ditimpa.
- Satu baris Excel D_K tetap satu TransactionDetail.
- KW tunggal tanpa DRPP wajib ditolak pada Upload Paket SPM.
- Invoice, faktur, BAST, SSP, dan bukti pendukung tidak boleh otomatis menjadi transaksi baru.
- Seluruh 15 kolom D_K wajib dipertahankan.
- Jangan menyatakan selesai hanya karena satu fixture atau satu PDF lulus.
- Jalankan integration test production path dan blind test sesuai spesifikasi.
- Setelah menguji OCR/parser dengan file nyata, langsung hapus seluruh cache yang dibuat oleh file uji tersebut berdasarkan hash sumber/halaman. Jangan menghapus cache dokumen lain.

## Sebelum Implementasi

1. Baca spesifikasi.
2. Audit kode terhadap spesifikasi.
3. Tentukan bagian Implemented, Partial, dan Missing.
4. Perbaiki sistem secara umum, bukan satu PDF.
5. Tambahkan test yang mengunci invariant umum.

## Batasan Operasi

Jangan melakukan:

- migration;
- reset database;
- commit;
- push;

kecuali diminta secara eksplisit oleh pengguna.

## Laporan Akhir

Laporkan:

- desain/fungsi yang diubah;
- aturan spesifikasi yang diselesaikan;
- test yang dijalankan;
- hasil regression dan blind test;
- kegagalan yang masih tersisa;
- bukti tidak ada hardcode sampel di production code.

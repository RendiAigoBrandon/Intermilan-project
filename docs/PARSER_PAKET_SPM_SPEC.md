
# Spesifikasi Parser Paket SPM INTERMILAN

## 1. Tujuan

Sistem memproses SPM, SPP, SP2D, DRPP, KW, Lampiran COA, SSP, faktur, invoice, BAST, dan bukti pendukung secara umum.

Prinsip hasil:

- data benar boleh diproses otomatis;
- data meragukan wajib Perlu Review;
- tidak boleh ada hasil salah yang dianggap valid;
- sampel PDF hanya regression corpus, bukan hardcode.

## 2. Larangan Production Code

Dilarang menggunakan:

- filename tertentu;
- nomor SPM/DRPP/KW tertentu;
- nominal/deskripsi sampel;
- nomor halaman tetap milik satu PDF;
- koordinat absolut milik satu sampel;
- regex pembersih khusus satu dokumen;
- urutan halaman tetap;
- nomor DRPP sebagai nomor SPM;
- fallback flat-text regex untuk membuat transaksi ketika parser tabel gagal.

Expected value sampel hanya boleh berada di test/golden fixture.

## 3. Pipeline Dokumen

1. Lakukan identity probe ringan.
2. Ambil native text jika tersedia.
3. Jika PDF scan, render 300 DPI.
4. Klasifikasikan setiap halaman.
5. OCR hanya halaman/crop yang diperlukan.
6. Auto-rotate dan deskew hanya jika confidence rendah.
7. Gunakan TSV/bounding box dan posisi cell.
8. Parse dengan parser khusus jenis dokumen.
9. Normalisasi field.
10. Validasi identitas, baris, COA, dan total.
11. Hasil valid diproses; hasil meragukan masuk Perlu Review.
12. Cache OCR per file, halaman, crop, rotasi, dan versi preprocessing.

## 4. Page Classifier

Jenis halaman minimal:

- SPM_HEADER
- SPM_DETAIL
- SPP
- SP2D
- SP2D_DETAIL
- DRPP
- LAMPIRAN_COA
- KW_MAIN
- KW_SUPPORT
- INVOICE
- FAKTUR
- BAST
- SSP
- FORM_FP
- UNKNOWN

Classifier menggunakan judul, label kolom, struktur tabel, dan posisi relatif. Jangan mengandalkan filename.

Setiap halaman menyimpan:

- page_number;
- document_type;
- confidence;
- rotation;
- method;
- alasan klasifikasi.

## 5. Parser Registry

Setiap jenis dokumen mempunyai:

- classifier;
- extractor;
- normalizer;
- validator;
- preview serializer.

Parser satu jenis dokumen tidak boleh membaca wilayah tanggung jawab dokumen lain.

## 6. Parser Tabel Berbasis Cell

Untuk tabel:

1. Temukan area tabel dari header dan garis/grid.
2. Gunakan koordinat relatif terhadap ukuran halaman.
3. Tentukan batas kolom dan baris.
4. OCR per cell.
5. Kelompokkan kata berdasarkan posisi x/y.
6. Dukung multiline, tabel lintas halaman, dan header berulang.
7. Satu cell gagal tidak boleh menggeser baris berikutnya.
8. Jangan mengandalkan nomor urut berkelanjutan.
9. Gunakan konfigurasi OCR angka untuk akun/nominal.
10. Gunakan OCR teks untuk deskripsi.
11. Baris Jumlah/Total tidak boleh menjadi transaksi.
12. Nominal tidak boleh masuk COA/Pembebanan.
13. Deskripsi tidak boleh mengambil teks di luar batas cell.

Setiap field menyimpan:

- raw_value;
- normalized_value;
- source_page;
- bounding_box;
- document_type;
- method;
- confidence.

## 7. Relasi Dokumen

Relasi utama:

SPM
└── DRPP
    └── Item KW
        └── invoice/faktur/BAST/bukti pendukung

Invoice, faktur, kuitansi pendukung, BAST, SSP, dan foto tidak otomatis menjadi transaksi baru.

Item KW dibentuk dari tabel Bukti Pengeluaran DRPP. Dokumen pendukung hanya memperkaya item tersebut.

## 8. Aturan SPM

SPM mengambil:

- satker;
- nomor SPM;
- tanggal SPM;
- tahun;
- jenis SPM;
- cara pembayaran;
- bruto/netto;
- identitas SPP/SP2D jika tersedia.

Detail transaksi diambil hanya dari tabel detail yang tervalidasi. Jangan mengambil uraian umum, SSP, petunjuk formulir, tanda tangan, atau stempel sebagai deskripsi.

Nomor final dibandingkan dari:

- filename sebagai petunjuk, bukan bukti final;
- header SPM;
- SPP;
- SP2D;
- konteks D_K.

Jika bukti berbeda atau confidence rendah, Perlu Review.

## 9. Aturan DRPP

Header DRPP mengambil:

- nomor DRPP;
- satker;
- tahun;
- tanggal DRPP;
- total tercetak.

Tabel Bukti Pengeluaran mengambil:

- nomor KW/Bukti;
- tanggal;
- penerima;
- keperluan;
- NPWP;
- akun;
- jumlah.

Lampiran COA mengambil:

- COA lengkap;
- akun;
- rincian/item;
- deskripsi;
- nilai.

Bukti Pengeluaran dan Lampiran COA adalah struktur berbeda. Jangan menggandakan transaksi.

Setiap item mewarisi nomor DRPP parent.

Validasi:

- jumlah item;
- total item;
- total tercetak;
- akun;
- nomor bukti;
- relasi parent.

Jika tidak cocok, Perlu Review.

## 10. Aturan KW

KW tunggal dilarang pada Upload Paket SPM.

KW hanya boleh diproses jika:

- terdapat DRPP dalam upload yang sama;
- item KW tercantum dalam DRPP;
- DRPP terkait dengan Nomor SPM terverifikasi.

KW tanpa DRPP:

- ditolak sebelum OCR penuh;
- tidak membuat transaksi;
- tidak mengubah D_K;
- tidak menampilkan tombol Simpan ke D_K;
- tampilkan “KW/Bukti wajib diunggah bersama DRPP”.

KW tidak boleh menghasilkan Nomor SPM tebakan.

## 11. Existing D_K sebagai Sumber Utama

Sebelum OCR penuh, cari D_K berdasarkan:
satker + tahun + nomor SPM terverifikasi.

Jika D_K existing ditemukan:

- jangan menjalankan row builder 15 kolom;
- jangan membuat D_K baru;
- gunakan seluruh TransactionDetail dalam grup;
- proses hanya dokumen yang belum tersedia;
- field existing tidak boleh ditimpa;
- hanya field kosong boleh diisi jika confidence cukup;
- checklist diperbarui pada seluruh grup SPM;
- upload ulang harus idempotent.

Jika SPM belum terverifikasi:

- simpan file sebagai Perlu Review;
- tampilkan pilihan SPM;
- jangan mengubah D_K.

## 12. Matching DRPP ke D_K

Matching grup:
satker + tahun + nomor SPM + nomor DRPP.

Matching item:
nomor KW/Bukti + akun + nominal.

Status:

- satu exact candidate: Matched;
- nol kandidat: Unmatched;
- lebih dari satu: Conflict;
- kandidat unik hanya dari akun+nominal: Perlu Konfirmasi.

Jangan auto-match kandidat ambigu.

## 13. Sumber 15 Kolom D_K

- Helper: dihitung sistem.
- Akun: Detail SPM/COA/DRPP.
- SP2D Bulan: tanggal SP2D.
- Cara Pembayaran: SPM.
- Nomor SPM: SPM/SPP/SP2D terverifikasi.
- Tanggal SPM: SPM.
- Jenis SPM: SPM.
- No. Kuitansi: DRPP/KW.
- No. DRPP: header DRPP.
- Deskripsi: cell uraian Lampiran COA atau keperluan KW.
- Nilai Bruto: detail transaksi/DRPP.
- Nilai Netto: detail transaksi/SP2D.
- Pembebanan: COA valid.
- FP: Faktur Pajak.
- PPh21: SSP/tabel potongan.

Satu baris Excel D_K tetap satu TransactionDetail. Jangan menggabungkan berdasarkan akun atau nomor SPM.

## 14. Preview Editable

Preview DRPP harus editable.

Parent:

- No. DRPP;
- Satker;
- Tahun;
- Tanggal DRPP;
- Nomor SPM terkait.

Item:

- No. KW/Bukti;
- Tanggal;
- Penerima;
- NPWP;
- Akun;
- Jumlah;
- Keperluan.

Simpan Perubahan & Validasi Ulang:

- menyimpan ke parsed_data;
- tidak OCR ulang;
- menghitung ulang total;
- menjalankan ulang validasi dan matching.

## 15. Database D_K Accordion

Judul/header utama tetap.

Kelompokkan tampilan berdasarkan:
satker + tahun + nomor SPM.

Gunakan tombol ▶/▼.

Saat dibuka, tampilkan seluruh TransactionDetail asli dengan 15 kolom lengkap:

Helper, Akun, SP2D Bulan, Cara Pembayaran, Nomor SPM,
Tanggal SPM, Jenis SPM, No. Kuitansi, No. DRPP,
Deskripsi, Nilai Bruto, Nilai Netto, Pembebanan, FP, PPh21.

Grouping hanya untuk tampilan dan tidak boleh mengubah database.

Pagination berdasarkan grup SPM. Export tetap memuat seluruh baris asli. Detail boleh lazy-load.

## 16. Regression Corpus

Gunakan sebagai test, bukan hardcode:

- 00033A
- 00074T
- 00084T
- 00135T
- DRPP 00029
- DRPP 00030 KW 00209

Golden assertion khusus test:

DRPP 00029:

- 12 KW;
- total 30.195.422;
- no_drpp=00029.

DRPP 00030 KW 00209:

- page_count=31;
- kandidat SPM=00135T;
- no_drpp=00030;
- jumlah item DRPP=1;
- no_kw=00209/KW/019937/2026;
- akun=521811;
- total=3.558.750;
- invoice/faktur/BAST hanya support.

Production code tidak boleh mengandung expected value tersebut.

## 17. Pengujian Wajib

- real render PDF dan Tesseract;
- tidak patch extract_pdf_text pada integration test;
- variasi rotasi;
- halaman acak;
- noise;
- multiline;
- header berulang;
- titik/koma rupiah;
- cell gagal OCR;
- dokumen pendukung ganda;
- KW tanpa DRPP;
- SPM existing;
- matching conflict;
- upload ulang.

Jalankan minimal satu SPM dan satu DRPP blind test yang tidak digunakan saat development.

Blind test lulus jika benar atau Perlu Review. Silent wrong adalah kegagalan.

## 18. Definition of Done

- parser_v2 berbasis cell/koordinat menjadi jalur production;
- legacy flat-text regex tidak digunakan untuk auto-commit;
- jumlah baris dan total seluruh corpus tepat;
- tidak ada total sebagai transaksi;
- tidak ada nominal dalam Pembebanan;
- tidak ada deskripsi bocor;
- existing D_K tidak dibangun ulang;
- KW tunggal ditolak;
- DRPP/KW terhubung ke grup SPM yang benar;
- preview editable;
- accordion D_K menampilkan 15 kolom;
- blind test dijalankan;
- hasil meragukan masuk Perlu Review;
- tidak ada hardcode sampel.

## 19. Batasan Operasi

Jangan:

- migration;
- reset database;
- commit;
- push;

kecuali diminta secara eksplisit oleh pengguna.

# Golden dataset D_K

Folder ini menyimpan manifest anotasi, bukan PDF/ZIP sumber. Corpus nyata tetap
berada di luar repository karena memuat dokumen keuangan dan kemungkinan PII.

Setiap manifest wajib membekukan SHA-256 fixture, mempunyai `row_key` yang
berasal dari posisi struktural transaksi, serta mengisi evidence untuk 14 kolom
D_K. `helper` dilarang ditulis di manifest karena selalu dihitung dari
`akun + no_kuitansi`.

Nilai yang tidak tersedia ditulis sebagai `null` dengan availability
`CONFIRMED_ABSENT` atau `NOT_APPLICABLE`. Nilai yang tidak dapat dipastikan
ditulis `null` dengan availability `AMBIGUOUS`; parser wajib mengembalikannya
untuk review, bukan membuat nilai tebakan.

Local acceptance memakai corpus wajib: fixture hilang atau SHA berubah adalah
failure. CI biasa hanya boleh melewati suite `golden_ocr` secara eksplisit;
schema, comparator, serta synthetic/metamorphic tests tetap wajib berjalan.

Manifest tidak boleh dipakai untuk conditional production berdasarkan nama
file, hash, nomor sampel, DRPP, halaman tetap, nominal, `case_id`, atau urutan
entry ZIP.

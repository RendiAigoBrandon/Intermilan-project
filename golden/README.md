# Golden dataset D_K

Folder ini menyimpan manifest anotasi, bukan PDF/ZIP sumber. Corpus nyata tetap
berada di luar repository karena memuat dokumen keuangan dan kemungkinan PII.

Setiap manifest wajib membekukan SHA-256 fixture, mempunyai `row_key` yang
berasal dari posisi struktural transaksi, serta mengisi evidence untuk 14 kolom
D_K. `helper` dilarang ditulis di manifest karena selalu dihitung dari
`akun + no_kuitansi`.

Worksheet v2 memisahkan `parser_candidate`, `reviewer_expected`, dan
`reviewer_status`. Draft selalu dibuat sebagai `PENDING`; nilai kandidat parser
tidak disalin menjadi expected. Comparator acceptance hanya membaca
`reviewer_expected` pada transaksi berstatus `APPROVED` dan menolak `PENDING`
atau `REJECTED`.

Nilai yang tidak tersedia ditulis sebagai `null` dengan availability
`CONFIRMED_ABSENT` atau `NOT_APPLICABLE`. Nilai yang tidak dapat dipastikan
ditulis `null` dengan availability `AMBIGUOUS`; parser wajib mengembalikannya
untuk review, bukan membuat nilai tebakan.

Local acceptance memakai corpus wajib: fixture hilang atau SHA berubah adalah
failure. CI biasa hanya boleh melewati suite `golden_ocr` secara eksplisit;
schema, comparator, serta synthetic/metamorphic tests tetap wajib berjalan.
Direktori corpus dibaca dari `GOLDEN_FIXTURE_DIR`; nama lama
`GOLDEN_OCR_CORPUS_DIR` hanya fallback kompatibilitas. Draft anotasi harus
ditulis ke corpus eksternal, bukan di-commit ke repository, karena
`parser_candidate` dapat mengandung PII. Output report tetap meredaksi deskripsi.

Manifest tidak boleh dipakai untuk conditional production berdasarkan nama
file, hash, nomor sampel, DRPP, halaman tetap, nominal, `case_id`, atau urutan
entry ZIP.

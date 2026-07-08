# Excel Mapping INTERMILAN Tahap 1.2
Dokumen ini dibuat dari audit `Database awal.zip`. Sumber utama UI dan struktur data INTERMILAN Django adalah workbook Excel, bukan dashboard dekoratif bebas.
## Source of Truth
- `INTERMILAN.xlsx`: referensi utama dashboard, rekap, monitoring gabungan, user/home, dan integrasi data.
- `KK_13xx.xlsx`: referensi struktur data per satker/kabupaten/kota. Pola semua file KK harus dapat ditampung web.
- Jika ada variasi label antar workbook, web harus mempertahankan label Excel paling umum dan mencatat variasinya.

## Workbook Ditemukan
- `INTERMILAN.xlsx`: 8 sheet, tables=1, charts=0, pivots=0
- `KK_1300.xlsx`: 42 sheet, tables=31, charts=0, pivots=0
- `KK_1301.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1302.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1303.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1304.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1305.xlsx`: 43 sheet, tables=39, charts=0, pivots=0
- `KK_1306.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1307.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1308.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1309.xlsx`: 41 sheet, tables=39, charts=0, pivots=0
- `KK_1310.xlsx`: 41 sheet, tables=39, charts=0, pivots=0
- `KK_1311.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1312.xlsx`: 41 sheet, tables=39, charts=0, pivots=0
- `KK_1371.xlsx`: 41 sheet, tables=39, charts=0, pivots=0
- `KK_1372.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1373.xlsx`: 42 sheet, tables=40, charts=0, pivots=0
- `KK_1374.xlsx`: 41 sheet, tables=39, charts=0, pivots=0
- `KK_1375.xlsx`: 42 sheet, tables=39, charts=0, pivots=0
- `KK_1376.xlsx`: 41 sheet, tables=39, charts=0, pivots=0
- `KK_1377.xlsx`: 43 sheet, tables=40, charts=0, pivots=0

## Sheet Utama dan Frekuensi
- `Dashboard`: muncul di 21 workbook
- `Upload`: muncul di 20 workbook
- `Monitoring`: muncul di 20 workbook
- `D_K`: muncul di 20 workbook
- `521111-Belanja Keperluan Perkan`: muncul di 20 workbook
- `825111-825511`: muncul di 20 workbook
- `51XXXX-Belanja Pegawai`: muncul di 20 workbook
- `521115-Belanja Honor Operasiona`: muncul di 20 workbook
- `521211_Konsumsi_Rapat`: muncul di 20 workbook
- `521211-Belanja Bahan`: muncul di 20 workbook
- `521114-Belanja Pengiriman Surat`: muncul di 20 workbook
- `521119-Belanja Barang Operasion`: muncul di 20 workbook
- `521213-Honor Petugas`: muncul di 20 workbook
- `521213-Honor Pengajar`: muncul di 20 workbook
- `521219-Non Operasional Lainnya`: muncul di 20 workbook
- `521213-Honor Pokja`: muncul di 20 workbook
- `521219-Pengiriman Dokumen`: muncul di 20 workbook
- `521219-Asuransi`: muncul di 20 workbook
- `521252-Belanja Peralatan dan Me`: muncul di 20 workbook
- `521811-Persediaan`: muncul di 20 workbook
- `522111-Belanja Langganan Listri`: muncul di 20 workbook
- `522112-Belanja Langganan Telepo`: muncul di 20 workbook
- `522113-Belanja Langganan Air`: muncul di 20 workbook
- `522119-Belanja Langganan Daya d`: muncul di 20 workbook
- `522131-Belanja Jasa Konsultan`: muncul di 20 workbook
- `522141-Sewa`: muncul di 20 workbook
- `522151-Jasa Profesi`: muncul di 20 workbook
- `522191-Jasa Lainnya`: muncul di 20 workbook
- `523111-Belanja Pemeliharaan Ged`: muncul di 20 workbook
- `523119-Belanja Pemeliharaan Ged`: muncul di 20 workbook
- `523121-Belanja Pemeliharaan Per`: muncul di 20 workbook
- `523123-Belanja Barang Persediaa`: muncul di 20 workbook
- `524111-Perjadin Biasa`: muncul di 20 workbook
- `524113-Perjadin Dalam Kota`: muncul di 20 workbook
- `524114-Perjadin Paket Meeting D`: muncul di 20 workbook

## Header Penting dari Excel
### INTERMILAN.xlsx / Monitoring_Combine
`No`, `BPS Prov/Kab/Kota`, `Bulan SP2D`, `Realisasi FA 16 Detil Bulan ini (di isi satker)`, `Realisasi Intermilan Bulan ini`, `Realisasi Intermilan s.d Bulan Ini`, `Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)`, `Persentase Kelengkapan Dokumen`, `Persentase SPJ yang sudah di Upload`, `Persentase dokumen sudah di arsipkan`, `Deadline`, `Status`, `% Completed`, `BAR`, `TA`

### INTERMILAN.xlsx / Dashboard
`BPS Prov/Kab/Kota`, `Bulan SP2D`, `Realisasi FA 16 Detil Bulan ini (di isi satker)`, `Realisasi Intermilan Bulan ini`, `Realisasi Intermilan s.d Bulan Ini`, `Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)`, `Persentase Kelengkapan Dokumen`, `Persentase SPJ yang sudah di Upload`, `Persentase dokumen sudah di arsipkan`, `% Completed`, `TA`

### KK_13xx.xlsx / D_K
`Helper`, `Akun`, `SP2D Bulan`, `Cara Pembayaran`, `Nomor SPM`, `Tanggal SPM`, `Jenis SPM`, `No. Kuitansi`, `No. DRPP`, `Deskripsi`, `Nilai Bruto`, `Nilai Netto`, `Pembebanan`, `FP`, `PPh21`

### KK_13xx.xlsx / Upload
`No. SPM / Kuitansi`, `URL`

### KK_13xx.xlsx / Sheet Akun Belanja contoh 51XXXX-Belanja Pegawai
`Akun/BACK`, `SP2D Bulan`, `Cara Pembayaran`, `Nomor SPM`, `Tanggal SPM`, `Jenis SPM`, `No. Kuitansi (Hanya untuk dana UP/PTUP)/No. SPM`, `No. DRPP`, `Uraian Belanja per Transaksi`, `Nilai (Bruto)`, `Pembebanan`, `URL`, `SP2D`, `SPM`, `KAK`, `Form permintaan/ nota dinas`, `SPTJM (Khusus Tukin)`, `Rekapitulasi SPJ`, `Rekap Per Gol (Khusus Gaji)`, `Daftar Nominatif (SPJ)`, `Daftar Perubahan Gaji (Khusus Gaji)`, `Halaman Depan`, `SSP PPh 21`, `Realisasi BOS`, `% Completed`, `BAR`, `Dokumen pendukung yang belum ada`, `Catatan Tambahan Petugas, jika ada`

## Kolom yang Wajib Muncul di Web
- Dashboard/monitoring: `BPS Prov/Kab/Kota`, `Bulan SP2D`, realisasi FA 16, realisasi Intermilan bulan ini, realisasi s.d bulan ini, persentase realisasi, persentase kelengkapan dokumen, persentase SPJ upload, persentase arsip, `Deadline`, `Status`, `% Completed`, `TA`.
- D_K: `Helper`, `Akun`, `SP2D Bulan`, `Cara Pembayaran`, `Nomor SPM`, `Tanggal SPM`, `Jenis SPM`, `No. Kuitansi`, `No. DRPP`, `Deskripsi`, `Nilai Bruto`, `Nilai Netto`, `Pembebanan`, `FP`, `PPh21`.
- Upload dokumen: `No. SPM / Kuitansi`, `URL`.
- Checklist/sheet akun: kolom transaksi dasar, `URL`, dokumen pendukung per akun, `% Completed`, `BAR`, `Dokumen pendukung yang belum ada`, `Catatan Tambahan Petugas, jika ada`.

## Menu Web Berdasarkan Excel
- `Dashboard`: mengikuti `INTERMILAN.xlsx` sheet `Dashboard` dan `Monitoring_Combine`.
- `D_K`: mengikuti sheet `D_K` pada semua `KK_13xx.xlsx`.
- `Upload`: mengikuti sheet `Upload` pada `KK_13xx.xlsx`; di Django saat ini dipecah sebagai dokumen/checklist.
- `Monitoring`: mengikuti sheet `Monitoring` dan sheet akun belanja.
- `SP2D`: tetap diperlukan sebagai input/import SP2D, tetapi label tabel harus disejajarkan dengan alur Excel.
- `DRPP` dan `Paket SPM`: tetap modul lanjutan, tetapi tabelnya harus mengacu ke kolom `D_K`/sheet akun, bukan istilah baru.

## Rekomendasi Model Django Berdasarkan Excel
- `TransactionDetail` sudah mendekati sheet `D_K`, tetapi UI wajib memakai label Excel, bukan label teknis.
- `MonitoringSummary` menyimpan `INTERMILAN.xlsx` sheet `Monitoring_Combine` sebagai seed resmi Dashboard/Monitoring dan dapat di-refresh dari data web.
- Tambahkan di tahap berikutnya struktur metadata workbook/satker/sheet akun agar semua kolom dokumen per akun tidak hilang.
- `ChecklistTemplate` harus dapat berasal dari kolom dokumen di sheet akun belanja.
- `DocumentUpload` perlu mempertahankan pasangan `No. SPM / Kuitansi` dan `URL` dari sheet `Upload`.
- `DRPP` dan `PaketSPM` harus tetap terhubung ke `D_K`, bukan mengganti struktur Excel.

## Gap Model Django Saat Ini
- Belum ada model eksplisit untuk workbook/sheet Excel dan metadata kolom per satker.
- `SP2DRaw` belum langsung mewakili sheet di `Database awal.zip`; perlu disejajarkan dengan sumber SP2D yang dipakai operator.
- `ChecklistTemplate` masih generik; belum otomatis dibentuk dari kolom dokumen per akun.
- Dashboard Django membaca `MonitoringSummary` bila tersedia; fallback D_K hanya dipakai bila summary belum diimport.

## Clean Baseline PostgreSQL

- PostgreSQL rehearsal diarahkan sebagai clean baseline dari `Database awal.zip`.
- Count PostgreSQL tidak perlu sama dengan SQLite development yang kemungkinan berisi data test/legacy/manual.
- D_K dan checklist adalah data berbeda; selisih D_K 977 terhadap SQLite development bukan disebabkan file checklist.
- File SP2D raw test, DRPP test, ChecklistStatus test, dan Upload Paket SPM test tidak masuk seed clean baseline.

## MonitoringSummary

- Model: `apps.core.models.MonitoringSummary`.
- Import baseline: `import_monitoring_summary`.
- Refresh data harian: `refresh_monitoring_summary`.
- Dashboard dan Monitoring memakai `MonitoringSummary` jika tersedia.
- FA16 tetap data pembanding tersendiri dan tidak dihitung dari D_K.
- Nilai Intermilan dihitung dari D_K saat refresh.
- Persentase realisasi dihitung dari Intermilan terhadap FA16 jika FA16 > 0.
- Source row menunjukkan asal data: `excel_seed`, `calculated`, `manual`, atau `mixed`.
- User operator semua satker dibuat dengan `create_dev_users --all-satker` dari distinct satker aktif, bukan daftar hardcoded.

## Catatan Audit
- Audit metadata lengkap disimpan di `docs/excel_audit.json`.
- Beberapa workbook hasil ekspor Google Sheets tidak menyimpan dimensi eksplisit di XML; audit baris/kolom detail perlu proses cache/extract khusus jika dibutuhkan.
- Tidak ada chart/pivot XML terdeteksi; ada banyak table XML pada workbook KK.

## Batas Tahap Ini
- Tidak mengubah database lama.
- Tidak membuat parser PDF/OCR.
- Tidak membuat Upload Paket SPM penuh.
- Fokus pada mapping Excel dan UI spreadsheet-first.

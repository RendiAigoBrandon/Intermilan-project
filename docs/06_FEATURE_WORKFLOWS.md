# 06 - Feature Workflows

## Import Legacy SQLite

Dry-run:

```powershell
python manage.py import_legacy_sqlite --path "C:\Users\muall\Documents\Magang BPS\intermilan%20testing\INTERMILAN 29-06\instance\sp2d_kk1300.sqlite"
```

Import asli:

```powershell
python manage.py import_legacy_sqlite --path "C:\Users\muall\Documents\Magang BPS\intermilan%20testing\INTERMILAN 29-06\instance\sp2d_kk1300.sqlite" --commit
```

Replace hanya jika disetujui:

```powershell
python manage.py import_legacy_sqlite --path "..." --commit --replace-confirmed
```

## Import Excel Seed

Dry-run:

```powershell
python manage.py import_excel_seed --path "C:\Users\muall\Documents\Magang BPS\drive-download-20260615T061835Z-3-001"
```

Import asli:

```powershell
python manage.py import_excel_seed --path "C:\Users\muall\Documents\Magang BPS\drive-download-20260615T061835Z-3-001" --commit
```

## Duplicate Policy

- Default command tidak menimpa data.
- Duplikat dihitung dan diskip.
- Replace hanya memakai `--replace-confirmed`.

## Role Access Tahap 2.3

Helper permission berada di `apps/accounts/access.py`.

- Admin: akses semua satker, Review Data, import, upload, edit, dan export.
- Operator Satker: edit/upload hanya satker sendiri; monitoring lintas satker tetap read-only untuk satker lain.
- Viewer: read-only, tanpa upload/edit/delete/import/cleanup/fix.

Command user development:

```powershell
python manage.py create_dev_users
python manage.py create_dev_users --password "bps12345" --all-satker
```

Command ini hanya berjalan saat `DEBUG=True`. Tanpa `--all-satker`, command membuat/merapikan user `admin`, `operator_1300`, `operator_1301`, dan `viewer`. Dengan `--all-satker`, command membuat operator untuk semua satker aktif dari `MonitoringSummary` dan/atau D_K. Password default development wajib diganti sebelum penggunaan nyata.

## Checklist Dokumen & DRPP

- D_K adalah pusat akses checklist dokumen dan DRPP.
- `/documents/` hanya halaman arahan singkat ke D_K, bukan daftar transaksi duplikat.
- Detail checklist wajib memakai `transaction_id` lewat `/documents/<transaction_id>/`.
- D_K menautkan tombol Checklist ke id transaksi yang sama, bukan ke transaksi default.
- Tombol aksi mengikuti permission: Admin/Operator pemilik satker dapat membuka untuk edit/upload, sedangkan Viewer atau operator satker lain hanya read-only.
- Tombol DRPP di D_K hanya tampil bila transaksi memiliki No. DRPP, jenis/cara pembayaran mengandung GU/GUP/UP/TUP/PTUP/KKP, atau sudah terkait DRPPUpload/DRPPMatch.
- Route DRPP tahap ini memakai placeholder aman `/drpp/?transaction_id=<id>`; parser/upload penuh belum diaktifkan.

## Inbox SP2D vs Checklist Dokumen

- `/sp2d/` adalah inbox SP2D raw: status belum lengkap berarti SP2D belum cocok/masuk D_K atau belum punya detail akun.
- `/monitoring/` adalah rekap kelengkapan dokumen lintas satker dan tidak dipakai untuk edit checklist satu per satu.
- `/dk/` adalah pusat transaksi utama; dari sini user membuka detail checklist dan akses DRPP bersyarat.

## Filter Aktif

- `/sp2d/`: search mencari No SP2D, nomor invoice, nomor SPM extracted, deskripsi, kode satker, dan nama satker; filter Status Detail membedakan `Sudah Ada D_K` dan `Belum Ada Detail`.
- `/dk/`: search mencari SPM, kuitansi, DRPP, deskripsi, akun, kode/nama satker; filter tersedia untuk Satker, Bulan, Akun, Status Kelengkapan, dan `page_size`.
- `/monitoring/`: filter Satker, Bulan, Status, dan search memengaruhi ringkasan yang tampil.
- `/dashboard/`: filter `tahun`, `bulan`, dan `jenis_spm` memakai method GET; dropdown bulan selalu Januari-Desember; chart utama membandingkan satker pada bulan terpilih memakai `MonitoringSummary` bila tersedia; tabel Dashboard INTERMILAN memakai kolom Excel Dashboard dari preview D_K terbatas.

## Pagination D_K

- D_K memakai server-side pagination Django.
- Queryset difilter sesuai permission, search, dan filter terlebih dahulu, lalu dipaginasi.
- Jangan slice queryset sebelum paginator.
- Opsi `page_size`: 20, 50, dan 100 data/halaman.
- Link `Previous`, nomor halaman, dan `Next` memakai link normal dengan query parameter filter tetap terbawa.
- D_K detail tidak di-render ribuan baris sekaligus agar halaman tetap ringan.

## Dashboard MoM

- Kartu statistik Dashboard mengikuti scope role.
- Admin melihat agregasi semua satker.
- Operator Satker melihat data satker sendiri.
- Viewer melihat agregasi lintas satker secara read-only.
- Chart MoM utama memakai ringkasan resmi dari `MonitoringSummary`/`INTERMILAN.xlsx` sheet `Monitoring_Combine` jika tersedia.
- Fallback sementara boleh menghitung dari D_K aktif per `satker_code` pada bulan terpilih hanya jika data `MonitoringSummary` belum tersedia.
- X-axis chart utama memakai label `bps1300`, `bps1301`, dan seterusnya sesuai satker aktif.
- Operator Satker dapat melihat chart monitoring lintas satker secara read-only, tetapi data tabel/kartu operasional tetap dibatasi ke satker sendiri.
- Tooltip chart menampilkan bulan, dataset, nominal Rupiah, atau persentase.
- Sumber FA16 resmi berada di `Monitoring_Combine` kolom `Realisasi FA 16 Detil Bulan ini (di isi satker)`; sebelum data itu diimport, nilai FA16 tidak boleh dipalsukan dari D_K.

## MonitoringSummary

- Model `MonitoringSummary` menyimpan ringkasan Dashboard/Monitoring per satker, bulan, dan tahun.
- Baseline awal diimport dari `INTERMILAN.xlsx` sheet `Monitoring_Combine`.
- Unique key: `satker_code + bulan_number + tahun`.
- Metadata:
  - `source`: `excel_seed`, `calculated`, `manual`, atau `mixed`.
  - `last_refreshed_at`: waktu refresh terakhir.
  - `notes`: catatan opsional.

Import `Monitoring_Combine` adalah seed ringkasan resmi, bukan SP2D raw test, DRPP test, ChecklistStatus test, atau Upload Paket SPM test.

Import baseline:

```powershell
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" import_monitoring_summary --path "C:\Users\muall\Documents\INTERMILAN PROJECT\data_sources\Database awal\drive-download-20260615T061835Z-3-001" --commit
```

Refresh manual setelah data baru:

```powershell
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" refresh_monitoring_summary --tahun 2026 --bulan 6
py -c "import runpy; runpy.run_path('manage.py', run_name='__main__')" refresh_monitoring_summary --all
```

Aturan refresh:

- FA16 tidak dihitung dari D_K dan tidak dioverwrite oleh refresh D_K.
- Intermilan bulan ini dihitung dari D_K bulan terkait.
- Intermilan s.d bulan ini dihitung dari D_K sampai bulan terkait.
- Persentase realisasi dihitung hanya jika FA16 > 0.
- Kelengkapan dokumen dan SPJ upload dihitung hanya jika data checklist/dokumen tersedia.

## Alur Sheet

- Sheet/daftar SP2D menjadi Inbox SP2D raw.
- Sheet D_K menjadi Detail Keuangan utama.
- Ringkasan Excel menjadi Dashboard dan Monitoring.
- Inbox SP2D tidak boleh menjadi duplikasi D_K.

## Tabel Utama Mengikuti Excel

- Dashboard INTERMILAN menampilkan kolom Excel Dashboard: Bulan SP2D, Cara Pembayaran, Nomor SPM, Jenis SPM, No. Kuitansi (Hanya untuk dana UP/PTUP)/No. SPM, No. DRPP, Uraian Belanja per Transaksi, Nilai (Bruto), Pembebanan, dan % Kelengkapan.
- Dashboard chart dan kartu ringkasan boleh memakai `MonitoringSummary`, tetapi tabel transaksi Dashboard tetap tidak mengganti alur D_K sebagai pusat transaksi lengkap.
- Monitoring menampilkan kolom Excel lintas satker: BPS Prov/Kab/Kota (pilih sesuai satker msg2), Bulan SP2D, Realisasi FA 16 Detil Bulan ini (di isi satker), Realisasi Intermilan Bulan ini, Realisasi Intermilan s.d Bulan Ini, Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%), Persentase Kelengkapan Dokumen, Persentase SPJ yang sudah di Upload, Apakah sudah di arsipkan? (V) Sudah ( ) Belum, Deadline, Status, % Completed, dan BAR.
- D_K menampilkan urutan utama Excel: Helper, Akun, SP2D Bulan, Cara Pembayaran, Nomor SPM, Tanggal SPM, Jenis SPM, No. Kuitansi, No. DRPP, Deskripsi, Nilai Bruto, Nilai Netto, Pembebanan, FP, dan PPh21.
## Workflow Upload Aman

Semua upload baru memakai pola:

1. Upload file.
2. Parse atau OCR fallback.
3. Preview hasil baca.
4. Commit manual oleh user.
5. Cancel/cleanup jika tidak jadi.

Catatan implementasi:

- Upload SP2D Excel tidak langsung membuat `SP2DRaw`; data baru masuk setelah user menekan `Commit Import`.
- Upload DRPP PDF membuat `DRPPUpload` dan `DRPPItem` hanya setelah commit.
- Upload Paket SPM PDF/ZIP membuat `PaketSPMUpload` dan `PaketSPMPreviewItem` setelah commit, dan tidak mengubah baseline `TransactionDetail / D_K`.
- Cleanup command bersifat dry-run secara default:
  - `cleanup_sp2d_upload_test`
  - `cleanup_drpp_upload_test`
  - `cleanup_paket_spm_upload_test`

## OCR Fallback

Parser PDF menggunakan dependency opsional:

- PyMuPDF atau pdfplumber untuk text layer.
- pytesseract dan pdf2image untuk OCR scan.
- Tesseract binary dan Poppler perlu diinstal terpisah di Windows jika OCR scan ingin diaktifkan.

Jika dependency tidak tersedia, halaman preview tetap berjalan dan menampilkan warning setup.

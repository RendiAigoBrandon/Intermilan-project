# Database Design Tahap 1

Model Django dipisahkan per domain agar tidak mengulang struktur monolitik Flask lama.

## Relasi Utama

- `accounts.Profile` memperluas user Django dengan role dan akses satker.
- `sp2d.SP2DRaw` menyimpan data mentah hasil import SP2D.
- `dk.TransactionDetail` menyimpan D_K dan dapat terhubung ke `SP2DRaw`.
- `documents.ChecklistStatus` dan `documents.DocumentUpload` terhubung ke `TransactionDetail`.
- `drpp.DRPPUpload` mewakili satu DRPP pada alur SATKER -> SPM -> DRPP -> banyak item/KW.
- `drpp.DRPPItem` menyimpan item/KW di bawah DRPP.
- `drpp.DRPPMatch` menyimpan hasil matching item DRPP ke `TransactionDetail`.
- `paket_spm.PaketSPMUpload` dan `PaketSPMPreviewItem` baru kerangka awal untuk tahap lanjutan.
- `auditlog.AuditLog` menyimpan aktivitas penting user.

## Catatan Legacy

Database lama `instance/sp2d_kk1300.sqlite` tidak diubah langsung. Migrasi dilakukan dari script terpisah dengan koneksi read-only.

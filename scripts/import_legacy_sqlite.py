"""Kerangka migrasi read-only dari SQLite Flask lama ke model Django.

Tahap 1 sengaja belum memuat parser/OCR atau aturan matching berat. Script ini
menyiapkan jalur aman untuk membaca database lama tanpa mengubah file sumber.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.development")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.dk.models import TransactionDetail  # noqa: E402
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentUpload  # noqa: E402
from apps.drpp.models import DRPPItem, DRPPMatch, DRPPUpload  # noqa: E402
from apps.sp2d.models import SP2DImportBatch, SP2DRaw  # noqa: E402


TABLES = [
    "imports",
    "sp2d_raw",
    "transaksi_detail",
    "checklist_template",
    "checklist_status",
    "dokumen_upload",
    "drpp_upload",
    "drpp_item",
    "drpp_match",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Import legacy INTERMILAN SQLite database.")
    parser.add_argument("--legacy-db", default=os.environ.get("LEGACY_SQLITE_PATH", ""), help="Path ke sp2d_kk1300.sqlite lama.")
    parser.add_argument("--dry-run", action="store_true", help="Hitung dan validasi mapping tanpa menulis ke database Django.")
    return parser.parse_args()


def connect_readonly(path: str):
    db_path = Path(path).expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database legacy tidak ditemukan: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def rows(conn, table):
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(f"SELECT * FROM {table}"))
    except sqlite3.OperationalError:
        return []


def as_decimal(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return Decimal("0")


def as_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def as_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt).date()
        except ValueError:
            continue
    return None


def normalize_status(value, default):
    if not value:
        return default
    normalized = str(value).upper().replace(" ", "_").replace("-", "_")
    return normalized[:20]


def import_data(conn, dry_run=False):
    report = {"success": 0, "failed": 0, "skipped": 0}
    legacy_imports = {}
    legacy_sp2d = {}
    legacy_details = {}
    legacy_docs = {}
    legacy_drpp = {}
    legacy_drpp_items = {}

    table_counts = {table: len(rows(conn, table)) for table in TABLES}
    print("Legacy table counts:")
    for table, count in table_counts.items():
        print(f"- {table}: {count}")

    if dry_run:
        print("Dry-run aktif. Tidak ada data yang ditulis ke database Django.")
        return report

    with transaction.atomic():
        for row in rows(conn, "imports"):
            try:
                batch = SP2DImportBatch.objects.create(
                    filename=row["filename"] or "",
                    original_filename=row["original_name"] or row["filename"] or "",
                    tahun=as_int(row["tahun"]),
                    bulan=as_int(row["bulan"]),
                    total_rows=as_int(row["total_rows"]) or 0,
                    status=SP2DImportBatch.Status.COMPLETED,
                    notes="Migrated from legacy SQLite.",
                )
                legacy_imports[row["id"]] = batch
                report["success"] += 1
            except Exception as exc:
                report["failed"] += 1
                print(f"Gagal migrasi imports id={row['id']}: {exc}")

        for row in rows(conn, "sp2d_raw"):
            try:
                raw = SP2DRaw.objects.create(
                    import_batch=legacy_imports.get(row["import_id"]),
                    satker_name=row["nama_satker"] or "",
                    no_sp2d=row["no_sp2d"] or "",
                    tanggal_selesai_sp2d=as_date(row["tanggal_selesai_sp2d"]),
                    tgl_sp2d=as_date(row["tgl_sp2d"]),
                    mata_uang=row["mata_uang"] or "",
                    nilai_spm=as_decimal(row["nilai_spm"]),
                    potongan=as_decimal(row["potongan"]),
                    nilai_sp2d=as_decimal(row["nilai_sp2d"]),
                    nomor_invoice=row["nomor_invoice"] or "",
                    tanggal_invoice=as_date(row["tanggal_invoice"]),
                    jenis_spm=row["jenis_spm"] or "",
                    jenis_sp2d=row["jenis_sp2d"] or "",
                    deskripsi=row["deskripsi"] or "",
                    cek_akun=row["cek_akun"] or "",
                    nomor_spm_extracted=row["nomor_spm_extracted"] or "",
                    bulan_sp2d=as_int(row["bulan_sp2d"]),
                    status=SP2DRaw.Status.PERLU_DETAIL,
                )
                legacy_sp2d[row["id"]] = raw
                report["success"] += 1
            except Exception as exc:
                report["failed"] += 1
                print(f"Gagal migrasi sp2d_raw id={row['id']}: {exc}")

        for row in rows(conn, "transaksi_detail"):
            try:
                detail = TransactionDetail.objects.create(
                    sp2d_raw=legacy_sp2d.get(row["sp2d_raw_id"]),
                    akun=row["akun"] or "-",
                    kategori=row["kategori"] or "",
                    bulan_sp2d=as_int(row["bulan_sp2d"]),
                    cara_pembayaran=row["cara_pembayaran"] or "",
                    nomor_spm=row["nomor_spm"] or "",
                    tanggal_spm=as_date(row["tanggal_spm"]),
                    jenis_spm=row["jenis_spm"] or "",
                    no_kuitansi=row["no_kuitansi"] or "",
                    no_drpp=row["no_drpp"] or "",
                    deskripsi=row["deskripsi"] or "",
                    nilai_bruto=as_decimal(row["nilai_bruto"]),
                    nilai_netto=as_decimal(row["nilai_netto"]),
                    pembebanan=row["pembebanan"] or "",
                    fp=row["fp"] or "",
                    pph21=as_decimal(row["pph21"]),
                    status_detail=TransactionDetail.StatusDetail.DRAFT,
                )
                legacy_details[row["id"]] = detail
                report["success"] += 1
            except Exception as exc:
                report["failed"] += 1
                print(f"Gagal migrasi transaksi_detail id={row['id']}: {exc}")

        for row in rows(conn, "checklist_template"):
            ChecklistTemplate.objects.create(
                nama_dokumen=row["nama_dokumen"] or "",
                kategori=row["kode_pattern"] or "",
                wajib=bool(row["wajib"]),
                urutan=as_int(row["prioritas"]) or 0,
                is_active=True,
            )
            report["success"] += 1

        for row in rows(conn, "dokumen_upload"):
            detail = legacy_details.get(row["transaksi_detail_id"])
            if not detail:
                report["skipped"] += 1
                continue
            doc = DocumentUpload.objects.create(
                transaction_detail=detail,
                document_type=row["nama_dokumen"] or "",
                original_filename=row["original_name"] or row["filename"] or "",
                stored_filename=row["filename"] or "",
                file=row["file_path"] or "",
            )
            legacy_docs[row["id"]] = doc
            report["success"] += 1

        for row in rows(conn, "checklist_status"):
            detail = legacy_details.get(row["transaksi_detail_id"])
            if not detail:
                report["skipped"] += 1
                continue
            ChecklistStatus.objects.create(
                transaction_detail=detail,
                nama_dokumen=row["nama_dokumen"] or "",
                wajib=bool(row["wajib"]),
                status=ChecklistStatus.Status.ADA if row["document_id"] else ChecklistStatus.Status.BELUM,
                dokumen_upload=legacy_docs.get(row["document_id"]),
            )
            report["success"] += 1

        for row in rows(conn, "drpp_upload"):
            upload = DRPPUpload.objects.create(
                transaction_detail=legacy_details.get(row["transaksi_detail_id"]),
                document_upload=legacy_docs.get(row["dokumen_upload_id"]),
                nomor_drpp=row["nomor_drpp"] or "",
                nomor_drpp_norm=(row["nomor_drpp"] or "").upper().replace(" ", ""),
                tanggal_drpp=as_date(row["tanggal_drpp"]),
                jenis_spp=row["jenis_spp"] or "",
                bulan=as_int(row["bulan"]),
                total_jumlah=as_decimal(row["total_jumlah"]),
                raw_text=row["raw_text"] or "",
                status_updated_at=timezone.now(),
            )
            legacy_drpp[row["id"]] = upload
            report["success"] += 1

        for row in rows(conn, "drpp_item"):
            item = DRPPItem.objects.create(
                drpp_upload=legacy_drpp[row["drpp_upload_id"]],
                no_urut=as_int(row["no_urut"]),
                no_bukti=row["no_bukti"] or "",
                no_bukti_norm=(row["no_bukti"] or "").upper().replace(" ", ""),
                tanggal_bukti=as_date(row["tanggal_bukti"]),
                penerima=row["penerima"] or "",
                keperluan=row["keperluan"] or "",
                npwp=row["npwp"] or "",
                akun=row["akun"] or "",
                jumlah=as_decimal(row["jumlah"]),
                catatan=row["catatan"] or "",
            )
            legacy_drpp_items[row["id"]] = item
            report["success"] += 1

        for row in rows(conn, "drpp_match"):
            DRPPMatch.objects.create(
                drpp_upload=legacy_drpp.get(row["drpp_upload_id"]),
                drpp_item=legacy_drpp_items.get(row["drpp_item_id"]),
                transaction_detail=legacy_details.get(row["transaksi_detail_id"]),
                status_match=DRPPMatch.StatusMatch.PERLU_DICEK,
                skor_match=as_decimal(row["skor_match"]),
                is_manual=bool(row["is_manual"]),
                catatan=row["catatan"] or "",
            )
            report["success"] += 1

    return report


def main():
    args = parse_args()
    conn = connect_readonly(args.legacy_db)
    try:
        report = import_data(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    print(f"Selesai. Berhasil={report['success']} Gagal={report['failed']} Skip={report['skipped']}")


if __name__ == "__main__":
    main()

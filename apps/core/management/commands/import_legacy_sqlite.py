from __future__ import annotations

import sqlite3
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Profile
from apps.auditlog.models import AuditLog
from apps.core.import_utils import ImportStats, clean_text, parse_date, parse_decimal, parse_month
from apps.dk.models import MasterAkun, TransactionDetail
from apps.documents.models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink
from apps.drpp.models import DRPPItem, DRPPMatch, DRPPUpload
from apps.sp2d.models import SP2DImportBatch, SP2DRaw


class Command(BaseCommand):
    help = "Import read-only data dari SQLite Flask lama ke model Django INTERMILAN."

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Path ke instance/sp2d_kk1300.sqlite lama.")
        parser.add_argument("--commit", action="store_true", help="Jalankan import asli. Default tanpa flag ini adalah dry-run.")
        parser.add_argument("--skip-duplicates", action="store_true", default=True, help="Skip data duplikat. Default aktif.")
        parser.add_argument("--replace-confirmed", action="store_true", help="Update data existing jika duplikat ditemukan.")
        parser.add_argument("--include-users", action="store_true", help="Import user lama dengan password unusable.")
        parser.add_argument("--limit", type=int, default=0, help="Batasi baris per tabel untuk audit cepat.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File SQLite tidak ditemukan: {path}")

        if options["replace_confirmed"] and not options["commit"]:
            raise CommandError("--replace-confirmed hanya boleh dipakai bersama --commit.")

        self.commit = options["commit"]
        self.replace = options["replace_confirmed"]
        self.limit = options["limit"]
        self.stats: list[ImportStats] = []

        self.stdout.write(self.style.WARNING("Mode: IMPORT ASLI") if self.commit else self.style.WARNING("Mode: DRY-RUN"))
        self.stdout.write(f"Source: {path}")

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            tables = {r["name"] for r in conn.execute("select name from sqlite_master where type='table'")}
            if options["include_users"] and "users" in tables:
                self.import_users(conn)
            if "master_akun" in tables:
                self.import_master_akun(conn)
            if "imports" in tables:
                self.import_batches(conn)
            if "sp2d_raw" in tables:
                self.import_sp2d(conn)
            if "transaksi_detail" in tables:
                self.import_transactions(conn)
            if "checklist_template" in tables:
                self.import_checklist_templates(conn)
            if "dokumen_upload" in tables:
                self.import_document_links(conn)
            if "checklist_status" in tables:
                self.import_checklist_status(conn)
            if "drpp_upload" in tables:
                self.import_drpp_upload(conn)
            if "drpp_item" in tables:
                self.import_drpp_items(conn)
            if "drpp_match" in tables:
                self.import_drpp_matches(conn)
            if "audit_log" in tables:
                self.import_audit_log(conn)
        finally:
            conn.close()

        self.print_summary()

    def rows(self, conn, table):
        sql = f"select * from {table}"
        if self.limit:
            sql += f" limit {int(self.limit)}"
        return conn.execute(sql).fetchall()

    def save_or_count(self, stats, instance, duplicate=False):
        if duplicate and not self.replace:
            stats.duplicates += 1
            stats.skipped += 1
            return
        if not self.commit:
            stats.success += 1
            return
        instance.save()
        stats.success += 1

    def import_users(self, conn):
        User = get_user_model()
        stats = ImportStats("users")
        for row in self.rows(conn, "users"):
            stats.read += 1
            username = clean_text(row["username"])
            if not username:
                stats.skipped += 1
                continue
            exists = User.objects.filter(username=username).exists()
            if exists and not self.replace:
                stats.duplicates += 1
                stats.skipped += 1
                continue
            if not self.commit:
                stats.success += 1
                continue
            user = User.objects.filter(username=username).first() or User(username=username)
            user.first_name = clean_text(row["full_name"])
            user.set_unusable_password()
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            role = clean_text(row["role"]).lower()
            profile.role = Profile.Role.ADMIN_PUSAT if role == "admin" else Profile.Role.SATKER if role == "satker" else Profile.Role.VIEWER
            profile.satker_code = clean_text(row["satker_code"])
            profile.satker_name = clean_text(row["full_name"])
            profile.must_change_password = True
            profile.save()
            stats.success += 1
        self.stats.append(stats)

    def import_master_akun(self, conn):
        stats = ImportStats("master_akun")
        for row in self.rows(conn, "master_akun"):
            stats.read += 1
            kode = clean_text(row["kode"])
            if not kode:
                stats.skipped += 1
                continue
            duplicate = MasterAkun.objects.filter(kode=kode).exists()
            obj = MasterAkun(
                kode=kode,
                nama_akun=clean_text(row["nama_akun"]) or kode,
                kategori=clean_text(row["kategori"]),
                source="legacy_sqlite",
            )
            if duplicate and self.replace and self.commit:
                obj = MasterAkun.objects.get(kode=kode)
                obj.nama_akun = clean_text(row["nama_akun"]) or kode
                obj.kategori = clean_text(row["kategori"])
                obj.source = "legacy_sqlite"
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_batches(self, conn):
        stats = ImportStats("imports")
        for row in self.rows(conn, "imports"):
            stats.read += 1
            duplicate = SP2DImportBatch.objects.filter(pk=row["id"]).exists()
            obj = SP2DImportBatch(
                id=row["id"],
                filename=clean_text(row["filename"]),
                original_filename=clean_text(row["original_name"]) or clean_text(row["filename"]),
                tahun=int(clean_text(row["tahun"]) or 0) or None,
                bulan=parse_month(row["bulan"]),
                total_rows=row["total_rows"] or 0,
                success_rows=row["total_rows"] or 0,
                status=SP2DImportBatch.Status.COMPLETED,
                notes=f"Imported from legacy satker {clean_text(row['satker_code'])}",
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_sp2d(self, conn):
        stats = ImportStats("sp2d_raw")
        for row in self.rows(conn, "sp2d_raw"):
            stats.read += 1
            duplicate = SP2DRaw.objects.filter(pk=row["id"]).exists()
            batch = SP2DImportBatch.objects.filter(pk=row["import_id"]).first()
            obj = SP2DRaw(
                id=row["id"],
                import_batch=batch,
                satker_code=clean_text(row["satker_code"]),
                satker_name=clean_text(row["nama_satker"]),
                no_sp2d=clean_text(row["no_sp2d"]),
                tanggal_selesai_sp2d=parse_date(row["tanggal_selesai_sp2d"]),
                tgl_sp2d=parse_date(row["tgl_sp2d"]),
                mata_uang=clean_text(row["mata_uang"]),
                nilai_spm=parse_decimal(row["nilai_spm"]),
                potongan=parse_decimal(row["potongan"]),
                nilai_sp2d=parse_decimal(row["nilai_sp2d"]),
                nomor_invoice=clean_text(row["nomor_invoice"]),
                tanggal_invoice=parse_date(row["tanggal_invoice"]),
                jenis_spm=clean_text(row["jenis_spm"]),
                jenis_sp2d=clean_text(row["jenis_sp2d"]),
                deskripsi=clean_text(row["deskripsi"]),
                cek_akun=clean_text(row["cek_akun"]),
                nomor_spm_extracted=clean_text(row["nomor_spm_extracted"]),
                bulan_sp2d=parse_month(row["bulan_sp2d"]),
                status=self.map_sp2d_status(row["status"]),
                original_file=clean_text(row["jenis_sp2d"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_transactions(self, conn):
        stats = ImportStats("transaksi_detail")
        for row in self.rows(conn, "transaksi_detail"):
            stats.read += 1
            duplicate = TransactionDetail.objects.filter(pk=row["id"]).exists()
            obj = TransactionDetail(
                id=row["id"],
                sp2d_raw=SP2DRaw.objects.filter(pk=row["sp2d_raw_id"]).first(),
                satker_code=clean_text(row["satker_code"]),
                akun=clean_text(row["akun"]) or "-",
                kategori=clean_text(row["kategori"]),
                bulan_sp2d=parse_month(row["bulan_sp2d"]),
                cara_pembayaran=clean_text(row["cara_pembayaran"]),
                nomor_spm=clean_text(row["nomor_spm"]),
                tanggal_spm=parse_date(row["tanggal_spm"]),
                jenis_spm=clean_text(row["jenis_spm"]),
                no_kuitansi=clean_text(row["no_kuitansi"]),
                no_drpp=clean_text(row["no_drpp"]),
                deskripsi=clean_text(row["deskripsi"]),
                nilai_bruto=parse_decimal(row["nilai_bruto"]),
                nilai_netto=parse_decimal(row["nilai_netto"]),
                pembebanan=clean_text(row["pembebanan"]),
                fp=clean_text(row["fp"]),
                pph21=parse_decimal(row["pph21"]),
                status_detail=self.map_detail_status(row["status_detail"]),
                drpp_status=self.map_drpp_status(row["drpp_status"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_checklist_templates(self, conn):
        stats = ImportStats("checklist_template")
        seen = set()
        for row in self.rows(conn, "checklist_template"):
            stats.read += 1
            nama = clean_text(row["nama_dokumen"])
            kategori = clean_text(row["kode_pattern"])
            key = (nama, kategori)
            if not nama or key in seen:
                stats.skipped += 1
                continue
            seen.add(key)
            duplicate = ChecklistTemplate.objects.filter(nama_dokumen=nama, kategori=kategori).exists()
            obj = ChecklistTemplate(
                nama_dokumen=nama,
                kategori=kategori,
                wajib=bool(row["wajib"]),
                urutan=row["prioritas"] or 0,
                is_active=True,
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_document_links(self, conn):
        stats = ImportStats("dokumen_upload_drive_links")
        for row in self.rows(conn, "dokumen_upload"):
            stats.read += 1
            url = clean_text(row["link_url"])
            if not url:
                stats.skipped += 1
                continue
            transaction = TransactionDetail.objects.filter(pk=row["transaksi_detail_id"]).first()
            duplicate = DocumentDriveLink.objects.filter(google_drive_url=url).exists()
            obj = DocumentDriveLink(
                transaction_detail=transaction,
                satker_code=transaction.satker_code if transaction else "",
                nomor_spm=transaction.nomor_spm if transaction else "",
                no_kuitansi=transaction.no_kuitansi if transaction else "",
                no_drpp=transaction.no_drpp if transaction else "",
                jenis_dokumen=clean_text(row["nama_dokumen"]),
                nama_file=clean_text(row["original_name"]) or clean_text(row["filename"]),
                google_drive_url=url,
                status=DocumentDriveLink.Status.AKTIF,
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_checklist_status(self, conn):
        stats = ImportStats("checklist_status")
        if self.commit:
            existing_pairs = set(
                ChecklistStatus.objects.values_list("transaction_detail_id", "nama_dokumen")
            )
            valid_transaction_ids = set(TransactionDetail.objects.values_list("id", flat=True))
            to_create = []
            for row in self.rows(conn, "checklist_status"):
                stats.read += 1
                transaction_id = row["transaksi_detail_id"]
                nama = clean_text(row["nama_dokumen"])
                if transaction_id not in valid_transaction_ids:
                    stats.skipped += 1
                    continue
                key = (transaction_id, nama)
                if key in existing_pairs:
                    stats.duplicates += 1
                    stats.skipped += 1
                    continue
                to_create.append(ChecklistStatus(
                    transaction_detail_id=transaction_id,
                    nama_dokumen=nama,
                    wajib=bool(row["wajib"]),
                    status=self.map_checklist_status(row["status"]),
                ))
                existing_pairs.add(key)
                if len(to_create) >= 2000:
                    ChecklistStatus.objects.bulk_create(to_create, batch_size=2000, ignore_conflicts=True)
                    stats.success += len(to_create)
                    to_create = []
            if to_create:
                ChecklistStatus.objects.bulk_create(to_create, batch_size=2000, ignore_conflicts=True)
                stats.success += len(to_create)
            self.stats.append(stats)
            return

        for row in self.rows(conn, "checklist_status"):
            stats.read += 1
            if not self.commit:
                stats.success += 1
                continue
            transaction = TransactionDetail.objects.filter(pk=row["transaksi_detail_id"]).first()
            if not transaction:
                stats.skipped += 1
                continue
            nama = clean_text(row["nama_dokumen"])
            duplicate = ChecklistStatus.objects.filter(transaction_detail=transaction, nama_dokumen=nama).exists()
            obj = ChecklistStatus(
                transaction_detail=transaction,
                nama_dokumen=nama,
                wajib=bool(row["wajib"]),
                status=self.map_checklist_status(row["status"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_drpp_upload(self, conn):
        stats = ImportStats("drpp_upload")
        for row in self.rows(conn, "drpp_upload"):
            stats.read += 1
            duplicate = DRPPUpload.objects.filter(pk=row["id"]).exists()
            obj = DRPPUpload(
                id=row["id"],
                transaction_detail=TransactionDetail.objects.filter(pk=row["transaksi_detail_id"]).first(),
                nomor_drpp=clean_text(row["nomor_drpp"]),
                nomor_drpp_norm=clean_text(row["nomor_drpp_norm"]),
                tanggal_drpp=parse_date(row["tanggal_drpp"]),
                jenis_spp=clean_text(row["jenis_spp"]),
                bulan=parse_month(row["bulan"]),
                tahun=int(clean_text(row["tahun"]) or 0) or None,
                satker_code=clean_text(row["satker_code"]),
                nomor_spm=clean_text(row["nomor_spm"]),
                total_jumlah=parse_decimal(row["total_jumlah"]),
                raw_text=clean_text(row["raw_text"]),
                match_status=self.map_match_status(row["match_status"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_drpp_items(self, conn):
        stats = ImportStats("drpp_item")
        for row in self.rows(conn, "drpp_item"):
            stats.read += 1
            if not self.commit:
                stats.success += 1
                continue
            upload = DRPPUpload.objects.filter(pk=row["drpp_upload_id"]).first()
            if not upload:
                stats.skipped += 1
                continue
            duplicate = DRPPItem.objects.filter(pk=row["id"]).exists()
            obj = DRPPItem(
                id=row["id"],
                drpp_upload=upload,
                no_urut=row["no_urut"],
                no_bukti=clean_text(row["no_bukti"]),
                no_bukti_norm=clean_text(row["no_bukti"]),
                tanggal_bukti=parse_date(row["tanggal_bukti"]),
                penerima=clean_text(row["penerima"]),
                keperluan=clean_text(row["keperluan"]),
                npwp=clean_text(row["npwp"]),
                akun=clean_text(row["akun"]),
                jumlah=parse_decimal(row["jumlah"]),
                status_verifikasi=self.map_item_status(row["status_verifikasi"]),
                catatan=clean_text(row["catatan"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_drpp_matches(self, conn):
        stats = ImportStats("drpp_match")
        for row in self.rows(conn, "drpp_match"):
            stats.read += 1
            if not self.commit:
                stats.success += 1
                continue
            upload = DRPPUpload.objects.filter(pk=row["drpp_upload_id"]).first()
            if not upload:
                stats.skipped += 1
                continue
            duplicate = DRPPMatch.objects.filter(pk=row["id"]).exists()
            obj = DRPPMatch(
                id=row["id"],
                drpp_upload=upload,
                drpp_item=DRPPItem.objects.filter(pk=row["drpp_item_id"]).first(),
                transaction_detail=TransactionDetail.objects.filter(pk=row["transaksi_detail_id"]).first(),
                status_match=self.map_drpp_match_status(row["status_match"]),
                skor_match=parse_decimal(row["skor_match"]),
                is_manual=bool(row["is_manual"]),
                catatan=clean_text(row["catatan"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def import_audit_log(self, conn):
        stats = ImportStats("audit_log")
        for row in self.rows(conn, "audit_log"):
            stats.read += 1
            duplicate = AuditLog.objects.filter(action=clean_text(row["action"]), object_id=clean_text(row["ref_id"]), description=clean_text(row["note"])).exists()
            obj = AuditLog(
                action=clean_text(row["action"]),
                model_name=clean_text(row["ref_table"]),
                object_id=clean_text(row["ref_id"]),
                description=clean_text(row["note"]),
            )
            self.save_or_count(stats, obj, duplicate)
        self.stats.append(stats)

    def map_sp2d_status(self, value):
        text = clean_text(value).lower()
        if "cocok" in text and "tidak" not in text:
            return SP2DRaw.Status.COCOK
        if "tidak" in text:
            return SP2DRaw.Status.TIDAK_COCOK
        if "draft" in text:
            return SP2DRaw.Status.DRAFT
        return SP2DRaw.Status.PERLU_DETAIL

    def map_detail_status(self, value):
        text = clean_text(value).lower()
        if "lengkap" in text:
            return TransactionDetail.StatusDetail.LENGKAP
        if "review" in text or "import" in text:
            return TransactionDetail.StatusDetail.PERLU_REVIEW
        return TransactionDetail.StatusDetail.DRAFT

    def map_drpp_status(self, value):
        text = clean_text(value).lower()
        if "cocok" in text:
            return TransactionDetail.DRPPStatus.COCOK
        if "upload" in text or "ada" in text:
            return TransactionDetail.DRPPStatus.ADA
        if "cek" in text:
            return TransactionDetail.DRPPStatus.PERLU_DICEK
        return TransactionDetail.DRPPStatus.BELUM_ADA

    def map_checklist_status(self, value):
        text = clean_text(value).lower()
        if text == "ada":
            return ChecklistStatus.Status.ADA
        if "tidak" in text:
            return ChecklistStatus.Status.TIDAK_PERLU
        return ChecklistStatus.Status.BELUM

    def map_match_status(self, value):
        text = clean_text(value).lower()
        if "cocok" in text:
            return DRPPUpload.MatchStatus.COCOK
        if "konflik" in text:
            return DRPPUpload.MatchStatus.KONFLIK
        if "cek" in text:
            return DRPPUpload.MatchStatus.PERLU_DICEK
        return DRPPUpload.MatchStatus.BELUM_DIPROSES

    def map_item_status(self, value):
        text = clean_text(value).lower()
        if "cocok" in text or "sesuai" in text:
            return DRPPItem.StatusVerifikasi.SESUAI
        if "tidak" in text:
            return DRPPItem.StatusVerifikasi.TIDAK_SESUAI
        if "review" in text:
            return DRPPItem.StatusVerifikasi.PERLU_REVIEW
        return DRPPItem.StatusVerifikasi.BELUM_DICEK

    def map_drpp_match_status(self, value):
        text = clean_text(value).lower()
        if "manual" in text:
            return DRPPMatch.StatusMatch.COCOK_MANUAL
        if "cocok" in text:
            return DRPPMatch.StatusMatch.COCOK_OTOMATIS
        if "konflik" in text:
            return DRPPMatch.StatusMatch.KONFLIK
        if "tidak" in text:
            return DRPPMatch.StatusMatch.TIDAK_ADA_DI_DK
        return DRPPMatch.StatusMatch.PERLU_DICEK

    def print_summary(self):
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("Ringkasan import legacy SQLite"))
        for stats in self.stats:
            self.stdout.write(
                f"- {stats.source}: read={stats.read}, success={stats.success}, "
                f"skip={stats.skipped}, duplicate={stats.duplicates}, failed={stats.failed}"
            )
            for error in stats.errors[:10]:
                self.stdout.write(self.style.ERROR(f"  {error}"))

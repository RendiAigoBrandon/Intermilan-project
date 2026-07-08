"""
Management command: cleanup_sp2d_upload_test

Menghapus data SP2D hasil upload test secara aman.
- TIDAK menghapus: TransactionDetail, MonitoringSummary, DocumentDriveLink,
  User, Profile, MasterAkun, ChecklistTemplate.
- Hanya menghapus SP2DImportBatch dan SP2DRaw yang cocok dengan filter.

Penggunaan:
    # Lihat berapa yang akan dihapus (aman, tidak mengubah data):
    python manage.py cleanup_sp2d_upload_test --dry-run

    # Hapus batch dari file sample test:
    python manage.py cleanup_sp2d_upload_test --commit

    # Hapus SEMUA SP2DImportBatch dan SP2DRaw (gunakan hati-hati):
    python manage.py cleanup_sp2d_upload_test --commit --all

    # Filter berdasarkan nama file:
    python manage.py cleanup_sp2d_upload_test --commit --filename sample_sp2d_upload_test

    # Hapus N batch terbaru:
    python manage.py cleanup_sp2d_upload_test --commit --last 2
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.sp2d.models import SP2DImportBatch, SP2DRaw


TEST_FILENAME_KEYWORDS = [
    "sample_sp2d_upload_test",
    "sample_sp2d",
    "dummy_sp2d",
    "_test_",
    "_sample_",
]


class Command(BaseCommand):
    help = "Bersihkan data SP2D upload test tanpa menyentuh data baseline D_K/Monitoring/Document."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Benar-benar hapus data (default: dry-run saja).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Hanya tampilkan rencana, tidak menghapus (default jika tidak ada --commit).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            default=False,
            help="Hapus SEMUA SP2DImportBatch dan SP2DRaw (bukan hanya test). Wajib pakai --commit.",
        )
        parser.add_argument(
            "--filename",
            type=str,
            default="",
            help="Filter: hanya hapus batch yang nama filenya mengandung string ini.",
        )
        parser.add_argument(
            "--last",
            type=int,
            default=0,
            help="Filter: hanya hapus N batch terbaru.",
        )
        parser.add_argument(
            "--batch-id",
            type=int,
            default=0,
            help="Filter: hanya hapus batch dengan ID ini.",
        )
        parser.add_argument(
            "--include-doclinks",
            action="store_true",
            default=False,
            help="Ikut hapus DocumentDriveLink hasil upload SP2D test berdasarkan filename/source SP2D.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        delete_all = options["all"]
        filename_filter = options["filename"].strip()
        last_n = options["last"]
        batch_id = options["batch_id"]
        include_doclinks = options["include_doclinks"]

        dry_run = not commit

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "=== DRY-RUN mode (tidak ada yang dihapus). "
                "Tambahkan --commit untuk benar-benar menghapus. ==="
            ))

        # -------------------------------------------------------------------
        # Tentukan queryset batch yang akan dihapus
        # -------------------------------------------------------------------
        qs = SP2DImportBatch.objects.all()

        if batch_id:
            qs = qs.filter(pk=batch_id)
            self.stdout.write(f"Filter: batch ID = {batch_id}")

        elif filename_filter:
            qs = qs.filter(original_filename__icontains=filename_filter)
            self.stdout.write(f"Filter: original_filename mengandung '{filename_filter}'")

        elif last_n:
            ids = list(qs.order_by("-uploaded_at").values_list("pk", flat=True)[:last_n])
            qs = qs.filter(pk__in=ids)
            self.stdout.write(f"Filter: {last_n} batch terbaru")

        elif delete_all:
            self.stdout.write(self.style.WARNING(
                "Filter: SEMUA SP2DImportBatch dan SP2DRaw (--all aktif)"
            ))

        else:
            # Default: hanya hapus batch yang nama file-nya cocok dengan keyword test
            filter_q = None
            from django.db.models import Q
            for kw in TEST_FILENAME_KEYWORDS:
                q = Q(original_filename__icontains=kw) | Q(filename__icontains=kw) | Q(notes__icontains="TEST UPLOAD")
                filter_q = q if filter_q is None else filter_q | q
            qs = qs.filter(filter_q)
            self.stdout.write(
                f"Filter: nama file mengandung keyword test "
                f"({', '.join(TEST_FILENAME_KEYWORDS)}) atau notes 'TEST UPLOAD'"
            )

        batch_count = qs.count()
        raw_count = SP2DRaw.objects.filter(import_batch__in=qs).count()
        filenames = list(qs.values_list("original_filename", flat=True))
        doclink_qs = None
        doclink_count = 0
        if include_doclinks and filenames:
            from django.db.models import Q
            from apps.documents.models import DocumentDriveLink

            doc_filter = Q(jenis_dokumen="SP2D_EXCEL") | Q(catatan__icontains="source=SP2D")
            filename_filter_q = None
            for name in filenames:
                q = Q(nama_file=name) | Q(catatan__icontains=name)
                filename_filter_q = q if filename_filter_q is None else filename_filter_q | q
            doclink_qs = DocumentDriveLink.objects.filter(doc_filter & filename_filter_q)
            doclink_count = doclink_qs.count()

        # -------------------------------------------------------------------
        # Tampilkan rencana
        # -------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  SP2DImportBatch yang akan dihapus : {batch_count}")
        self.stdout.write(f"  SP2DRaw yang akan dihapus         : {raw_count}")
        if include_doclinks:
            self.stdout.write(f"  DocumentDriveLink SP2D test       : {doclink_count}")
        self.stdout.write("=" * 60)

        if batch_count == 0:
            self.stdout.write(self.style.SUCCESS("Tidak ada data yang cocok. Tidak ada yang dihapus."))
            return

        # Tampilkan detail batch
        self.stdout.write("\nDetail batch:")
        for b in qs.order_by("-uploaded_at")[:20]:
            self.stdout.write(
                f"  [ID:{b.pk}] {b.original_filename} | "
                f"tahun={b.tahun} bulan={b.bulan} | "
                f"rows={b.total_rows} | upload={b.uploaded_at:%Y-%m-%d %H:%M}"
            )

        # -------------------------------------------------------------------
        # Proteksi: verifikasi baseline tidak disentuh
        # -------------------------------------------------------------------
        try:
            from apps.dk.models import TransactionDetail
            from apps.core.models import MonitoringSummary
            from apps.documents.models import DocumentDriveLink

            dk_count = TransactionDetail.objects.count()
            ms_count = MonitoringSummary.objects.count()
            doc_count = DocumentDriveLink.objects.count()
            self.stdout.write(
                f"\nBaseline check (tidak akan disentuh): "
                f"D_K={dk_count}, MonitoringSummary={ms_count}, DocumentDriveLink={doc_count}"
            )
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Baseline check skip: {e}"))

        # -------------------------------------------------------------------
        # Eksekusi
        # -------------------------------------------------------------------
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\nDRY-RUN selesai. Jalankan dengan --commit untuk menghapus."
            ))
            return

        # Konfirmasi
        if delete_all and not batch_id and not filename_filter:
            confirm = input(
                f"\nAnda akan menghapus SEMUA {batch_count} batch dan {raw_count} raw. "
                f"Ketik 'YA' untuk lanjut: "
            )
            if confirm.strip() != "YA":
                self.stdout.write(self.style.WARNING("Dibatalkan oleh user."))
                return

        with transaction.atomic():
            deleted_raw, _ = SP2DRaw.objects.filter(import_batch__in=qs).delete()
            deleted_batch, _ = qs.delete()
            deleted_doclinks = 0
            if include_doclinks and doclink_qs is not None:
                deleted_doclinks, _ = doclink_qs.delete()

        self.stdout.write(self.style.SUCCESS(
            f"\nBerhasil dihapus: {deleted_batch} batch, {deleted_raw} raw rows, {deleted_doclinks if include_doclinks else 0} document links."
        ))

        # Verifikasi baseline masih aman setelah delete
        try:
            dk_after = TransactionDetail.objects.count()
            ms_after = MonitoringSummary.objects.count()
            doc_after = DocumentDriveLink.objects.count()
            self.stdout.write(
                f"Baseline setelah hapus: D_K={dk_after}, "
                f"MonitoringSummary={ms_after}, DocumentDriveLink={doc_after}"
            )
            assert dk_after == dk_count, f"ALERT: D_K berubah! {dk_count} -> {dk_after}"
            assert ms_after == ms_count, f"ALERT: MonitoringSummary berubah! {ms_count} -> {ms_after}"
            expected_doc_after = doc_count - (deleted_doclinks if include_doclinks else 0)
            assert doc_after == expected_doc_after, f"ALERT: DocumentDriveLink berubah di luar target! expected {expected_doc_after}, actual {doc_after}"
            self.stdout.write(self.style.SUCCESS("Baseline aman. Tidak ada data D_K/Monitoring/Document yang terpengaruh."))
        except AssertionError as e:
            self.stdout.write(self.style.ERROR(str(e)))

import shutil
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Profile
from apps.core.parsers import normalized_bukti_key
from apps.dk.models import TransactionDetail
from apps.drpp.models import DRPPSupportingAttachment, DRPPUpload
from apps.sp2d.models import SP2DRaw

from .models import DocumentDriveLink, DocumentUpload


User = get_user_model()


class SidebarNavigationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="archive-admin",
            password="password",
            email="",
        )
        self.client.force_login(self.admin)

    def test_sidebar_has_exact_final_admin_menu_order(self):
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        sidebar = content.split('<nav id="sidebarPanel"', 1)[1].split("</nav>", 1)[0]
        # Labels that exist in the actual sidebar
        labels = (
            "Home",
            "Dashboard",
            "D_K",
            "Upload SP2D",
            "Upload DRPP dan Kuitansi",
            "Arsip",
            "Monitoring",
            "Akun Keuangan",  # Present for admin
            "Peraturan",
            "Template",
            "Panduan",
            "Logout",
        )
        positions = [sidebar.index(f">{label}<") for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Upload Paket SPM", sidebar)
        self.assertNotIn("Detail Keuangan", sidebar)
        self.assertIn(f'href="{reverse("dk:transaction_list")}"', sidebar)

    def test_every_visible_admin_menu_destination_responds(self):
        destinations = (
            "core:home",
            "core:dashboard",
            "dk:transaction_list",
            "sp2d:list",
            "paket_spm:list",
            "documents:archive",
            "core:audit_data",
            "core:monitoring",
            "core:master_akun",
            "core:peraturan",
            "core:template",
            "core:panduan",
            "reports:index",
        )
        for destination in destinations:
            with self.subTest(destination=destination):
                response = self.client.get(reverse(destination))
                self.assertEqual(response.status_code, 200)

        logout = self.client.post(reverse("accounts:logout"))
        self.assertLess(logout.status_code, 400)


class DocumentArchiveTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="archive-admin",
            password="password",
            email="",
        )
        self.operator = User.objects.create_user(username="operator-1300", password="password")
        self.operator.profile.role = Profile.Role.SATKER
        self.operator.profile.satker_code = "1300"
        self.operator.profile.save(update_fields=["role", "satker_code"])

    def create_link(self, **overrides):
        values = {
            "satker_code": "1300",
            "nomor_spm": "00203A",
            "no_kuitansi": "00308/KW/019937/2026",
            "no_drpp": "00062",
            "jenis_dokumen": "KW",
            "nama_file": "DRPP 00062 KW 00308.pdf",
            "google_drive_url": "https://drive.google.com/file/d/archive-example/view",
            "status": DocumentDriveLink.Status.AKTIF,
            "created_by": self.admin,
        }
        values.update(overrides)
        return DocumentDriveLink.objects.create(**values)

    def test_archive_renders_clickable_drive_link_and_prefers_receipt_number(self):
        link = self.create_link()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("documents:archive"))

        self.assertEqual(response.status_code, 200)
        rendered = response.context["page_obj"].object_list[0]
        self.assertEqual(rendered.archive_number, link.no_kuitansi)
        self.assertContains(response, "No. SPM / Kuitansi")
        self.assertContains(
            response,
            f'href="{link.google_drive_url}" target="_blank" rel="noopener noreferrer"',
            html=False,
        )

    def test_archive_falls_back_to_spm_without_creating_synthetic_number(self):
        link = self.create_link(no_kuitansi="", nomor_spm="00182A")
        empty_number = self.create_link(
            no_kuitansi="",
            nomor_spm="",
            nama_file="tanpa-nomor.pdf",
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("documents:archive"))
        rendered = {item.pk: item for item in response.context["page_obj"].object_list}

        self.assertEqual(rendered[link.pk].archive_number, "00182A")
        self.assertEqual(rendered[empty_number.pk].archive_number, "")

    def test_invalid_url_is_plain_text_and_marked_for_review(self):
        link = self.create_link(google_drive_url="https://example.com/not-drive")
        self.client.force_login(self.admin)

        response = self.client.get(reverse("documents:archive"))
        active_filter = self.client.get(
            reverse("documents:archive"),
            {"status": DocumentDriveLink.Status.AKTIF},
        )
        review_filter = self.client.get(
            reverse("documents:archive"),
            {"status": DocumentDriveLink.Status.PERLU_DICEK},
        )
        rendered = response.context["page_obj"].object_list[0]

        self.assertFalse(rendered.archive_url_valid)
        self.assertEqual(rendered.archive_status, DocumentDriveLink.Status.PERLU_DICEK)
        self.assertNotContains(response, f'href="{link.google_drive_url}"')
        self.assertContains(response, "PERLU_DICEK")
        self.assertEqual(active_filter.context["paginator"].count, 0)
        self.assertEqual(review_filter.context["paginator"].count, 1)

    def test_operator_cannot_view_or_select_another_satker(self):
        own = self.create_link(nama_file="arsip-1300.pdf")
        other = self.create_link(
            satker_code="1301",
            nama_file="arsip-1301.pdf",
            google_drive_url="https://drive.google.com/file/d/other/view",
        )
        self.client.force_login(self.operator)

        response = self.client.get(reverse("documents:archive"))
        forced_filter = self.client.get(reverse("documents:archive"), {"satker": "1301"})

        self.assertContains(response, own.nama_file)
        self.assertNotContains(response, other.nama_file)
        self.assertEqual(response.context["satker_options"], ["1300"])
        self.assertEqual(forced_filter.context["paginator"].count, 0)

    def test_search_filters_and_pagination(self):
        for index in range(26):
            self.create_link(
                no_kuitansi=f"KW-{index:03d}",
                nama_file=f"arsip-{index:03d}.pdf",
                status=(
                    DocumentDriveLink.Status.PERLU_DICEK
                    if index == 25
                    else DocumentDriveLink.Status.AKTIF
                ),
            )
        self.create_link(satker_code="1301", nama_file="target-satker-lain.pdf")
        self.client.force_login(self.admin)

        page_two = self.client.get(reverse("documents:archive"), {"satker": "1300", "page": 2})
        searched = self.client.get(reverse("documents:archive"), {"q": "arsip-025"})
        filtered = self.client.get(
            reverse("documents:archive"),
            {"satker": "1300", "status": DocumentDriveLink.Status.PERLU_DICEK},
        )

        self.assertEqual(page_two.context["paginator"].count, 26)
        self.assertEqual(len(page_two.context["page_obj"].object_list), 1)
        self.assertEqual(searched.context["paginator"].count, 1)
        self.assertEqual(filtered.context["paginator"].count, 1)

    def test_search_matches_each_supported_archive_identifier(self):
        self.create_link(
            nomor_spm="SPM-CARI-001",
            no_kuitansi="KW-CARI-002",
            no_drpp="DRPP-CARI-003",
            nama_file="FILE-CARI-004.pdf",
        )
        self.client.force_login(self.admin)

        for query in ("SPM-CARI-001", "KW-CARI-002", "DRPP-CARI-003", "FILE-CARI-004"):
            with self.subTest(query=query):
                response = self.client.get(reverse("documents:archive"), {"q": query})
                self.assertEqual(response.context["paginator"].count, 1)

    def test_archive_has_clear_empty_state(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("documents:archive"))
        self.assertContains(response, "Belum ada data arsip yang sesuai")

    def test_checklist_list_scopes_links_uploads_and_filter_choices(self):
        own_transaction = TransactionDetail.objects.create(satker_code="1300", akun="521111")
        other_transaction = TransactionDetail.objects.create(satker_code="1301", akun="521111")
        own_link = self.create_link(transaction_detail=own_transaction, nama_file="own-link.pdf", jenis_dokumen="OWN")
        other_link = self.create_link(
            transaction_detail=other_transaction,
            satker_code="1301",
            nama_file="other-link.pdf",
            jenis_dokumen="OTHER",
        )
        own_upload = DocumentUpload.objects.create(
            transaction_detail=own_transaction,
            document_type="KW",
            original_filename="own-upload.pdf",
            stored_filename="own-upload.pdf",
        )
        other_upload = DocumentUpload.objects.create(
            transaction_detail=other_transaction,
            document_type="KW",
            original_filename="other-upload.pdf",
            stored_filename="other-upload.pdf",
        )
        self.client.force_login(self.operator)

        response = self.client.get(reverse("documents:checklist"))
        manipulated = self.client.get(reverse("documents:checklist"), {"satker": "1301"})

        self.assertEqual(list(response.context["drive_links"]), [own_link])
        self.assertEqual(list(response.context["uploads"]), [own_upload])
        self.assertEqual(list(response.context["jenis_options"]), ["OWN"])
        self.assertNotIn(other_link, response.context["drive_links"])
        self.assertNotIn(other_upload, response.context["uploads"])
        self.assertEqual(list(manipulated.context["drive_links"]), [])

    def test_checklist_detail_rejects_another_satker_direct_url(self):
        own_transaction = TransactionDetail.objects.create(satker_code="1300", akun="521111")
        other_transaction = TransactionDetail.objects.create(satker_code="1301", akun="521111")
        self.client.force_login(self.operator)

        own_response = self.client.get(reverse("documents:checklist_detail", args=[own_transaction.pk]))
        other_response = self.client.get(reverse("documents:checklist_detail", args=[other_transaction.pk]))

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(other_response.status_code, 404)

    def test_admin_sees_all_checklist_links_and_uploads(self):
        transaction_1300 = TransactionDetail.objects.create(satker_code="1300", akun="521111")
        transaction_1301 = TransactionDetail.objects.create(satker_code="1301", akun="521111")
        links = [
            self.create_link(transaction_detail=transaction_1300, nama_file="admin-1300.pdf"),
            self.create_link(transaction_detail=transaction_1301, satker_code="1301", nama_file="admin-1301.pdf"),
        ]
        uploads = [
            DocumentUpload.objects.create(
                transaction_detail=transaction,
                document_type="KW",
                original_filename=f"admin-{transaction.satker_code}.pdf",
                stored_filename=f"admin-{transaction.satker_code}.pdf",
            )
            for transaction in (transaction_1300, transaction_1301)
        ]
        self.client.force_login(self.admin)

        response = self.client.get(reverse("documents:checklist"))

        self.assertEqual(set(response.context["drive_links"]), set(links))
        self.assertEqual(set(response.context["uploads"]), set(uploads))


class DRPPSupportingAttachmentTests(TestCase):
    def setUp(self):
        self.media_tmp = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_tmp)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_tmp, True)

        self.admin = User.objects.create_superuser(
            username="receipt-admin",
            password="password",
            email="",
        )
        self.operator = User.objects.create_user(username="operator-019937", password="password")
        self.operator.profile.role = Profile.Role.SATKER
        self.operator.profile.satker_code = "019937"
        self.operator.profile.save(update_fields=["role", "satker_code"])

        self.other_operator = User.objects.create_user(username="operator-020000", password="password")
        self.other_operator.profile.role = Profile.Role.SATKER
        self.other_operator.profile.satker_code = "020000"
        self.other_operator.profile.save(update_fields=["role", "satker_code"])

        self.no_drpp = "00043/DRPP/019937/2026"
        self.sp2d = SP2DRaw.objects.create(
            tahun=2026,
            satker_code="019937",
            satker_name="BPS Provinsi Sumatera Barat",
            no_sp2d="260100000030107",
            nomor_spm_extracted="00166T",
            nilai_sp2d=Decimal("52249851"),
            status=SP2DRaw.Status.COCOK,
        )
        self.tx1 = TransactionDetail.objects.create(
            sp2d_raw=self.sp2d,
            satker_code="019937",
            akun="522151",
            nomor_spm="00166T",
            tanggal_spm=date(2026, 6, 15),
            no_kuitansi="00243/KW/019937/2026",
            no_drpp=self.no_drpp,
            nilai_bruto=Decimal("1800000"),
            nilai_netto=Decimal("1800000"),
            status_detail=TransactionDetail.StatusDetail.LENGKAP,
            drpp_status=TransactionDetail.DRPPStatus.COCOK,
        )
        self.tx2 = TransactionDetail.objects.create(
            sp2d_raw=self.sp2d,
            satker_code="019937",
            akun="521115",
            nomor_spm="00166T",
            tanggal_spm=date(2026, 6, 15),
            no_kuitansi="00246/KW/019937/2026",
            no_drpp="00043",
            nilai_bruto=Decimal("1000000"),
            nilai_netto=Decimal("1000000"),
            status_detail=TransactionDetail.StatusDetail.LENGKAP,
            drpp_status=TransactionDetail.DRPPStatus.COCOK,
        )
        self.drpp_upload = DRPPUpload.objects.create(
            transaction_detail=self.tx1,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            nomor_spm="00166T",
            match_status=DRPPUpload.MatchStatus.COCOK,
            uploaded_by=self.operator,
        )

    def receipt_file(self, name="kuitansi.pdf", content=b"%PDF-1.4 receipt"):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def create_transaction(
        self,
        *,
        satker_code="019937",
        akun="521111",
        nomor_spm="00999T",
        no_drpp="00999/DRPP/019937/2026",
        no_kuitansi="00999/KW/019937/2026",
        tanggal_spm=date(2026, 6, 15),
        nilai=Decimal("1"),
        sp2d_raw=None,
    ):
        return TransactionDetail.objects.create(
            sp2d_raw=sp2d_raw,
            satker_code=satker_code,
            akun=akun,
            nomor_spm=nomor_spm,
            tanggal_spm=tanggal_spm,
            no_kuitansi=no_kuitansi,
            no_drpp=no_drpp,
            nilai_bruto=nilai,
            nilai_netto=nilai,
            status_detail=TransactionDetail.StatusDetail.LENGKAP,
            drpp_status=TransactionDetail.DRPPStatus.COCOK,
        )

    def create_other_satker_transaction(self):
        return self.create_transaction(
            satker_code="020000",
            no_drpp="00999/DRPP/020000/2026",
            no_kuitansi="00999/KW/020000/2026",
        )

    def upload_receipts(self, files, drpp_upload=None, satker=None, no_drpp=None, follow=False):
        data = {
            "action": "upload_receipts",
            "satker": satker,
            "no_drpp": no_drpp,
            "receipt_files": files,
        }
        if drpp_upload:
            data.update(
                {
                    "drpp_upload_id": drpp_upload.pk,
                    "satker": drpp_upload.satker_code,
                    "no_drpp": drpp_upload.nomor_drpp,
                }
            )
        elif satker is None and no_drpp is None:
            data.update({"satker": self.drpp_upload.satker_code, "no_drpp": self.drpp_upload.nomor_drpp})
        return self.client.post(
            reverse("documents:upload_kuitansi"),
            data,
            follow=follow,
        )

    def test_admin_satker_dropdown_uses_existing_dk_satkers(self):
        DRPPUpload.objects.all().delete()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("documents:upload_kuitansi"))
        codes = [item["code"] for item in response.context["satker_options"]]

        self.assertEqual(response.status_code, 200)
        self.assertIn("019937", codes)
        self.assertContains(response, "019937 - BPS Provinsi Sumatera Barat")

    def test_operator_satker_dropdown_is_scoped_to_own_satker(self):
        self.create_other_satker_transaction()
        self.client.force_login(self.operator)

        response = self.client.get(reverse("documents:upload_kuitansi"))
        codes = [item["code"] for item in response.context["satker_options"]]

        self.assertEqual(response.status_code, 200)
        self.assertIn("019937", codes)
        self.assertNotIn("020000", codes)
        self.assertContains(response, "019937 - BPS Provinsi Sumatera Barat")
        self.assertNotContains(response, "020000")

    def test_drpp_found_shows_metadata(self):
        self.client.force_login(self.operator)

        response = self.client.get(
            reverse("documents:upload_kuitansi"),
            {"satker": "019937", "no_drpp": self.no_drpp},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DRPP ditemukan")
        self.assertContains(response, "00166T")
        self.assertContains(response, "Jumlah transaksi")
        self.assertEqual(response.context["drpp_context"]["transaction_count"], 2)

    def test_parsed_drpp_full_search_matches_full_and_legacy_short_dk(self):
        drpp_number = "00042/DRPP/019937/2026"
        tx_full = self.create_transaction(
            akun="522151",
            nomor_spm="00166T",
            no_drpp=drpp_number,
            no_kuitansi="00243/KW/019937/2026",
            nilai=Decimal("1800000"),
            sp2d_raw=self.sp2d,
        )
        tx_short = self.create_transaction(
            akun="521115",
            nomor_spm="00166T",
            no_drpp="00042",
            no_kuitansi="00246/KW/019937/2026",
            nilai=Decimal("1000000"),
            sp2d_raw=self.sp2d,
        )
        drpp_upload = DRPPUpload.objects.create(
            transaction_detail=tx_full,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=drpp_number,
            nomor_drpp_norm=normalized_bukti_key(drpp_number),
            nomor_spm="00166T",
            match_status=DRPPUpload.MatchStatus.COCOK,
        )
        self.client.force_login(self.operator)

        response = self.client.get(
            reverse("documents:upload_kuitansi"),
            {"satker": "019937", "no_drpp": drpp_number},
        )
        upload_response = self.upload_receipts(
            [self.receipt_file("parsed-00042.pdf")],
            satker="019937",
            no_drpp=drpp_number,
            follow=True,
        )
        attachment = DRPPSupportingAttachment.objects.get(document_upload__original_filename="parsed-00042.pdf")

        self.assertContains(response, "DRPP ditemukan")
        self.assertEqual(response.context["drpp_context"]["transaction_count"], 2)
        self.assertEqual(response.context["drpp_context"]["drpp_upload"], drpp_upload)
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(attachment.drpp_upload, drpp_upload)
        self.assertEqual(attachment.nomor_drpp_norm, "00042")

    def test_parsed_drpp_lookup_uses_dk_when_parent_metadata_is_legacy(self):
        drpp_number = "00042/DRPP/019937/2026"
        self.create_transaction(
            akun="522151",
            nomor_spm="00166T",
            no_drpp=drpp_number,
            no_kuitansi="00243/KW/019937/2026",
            nilai=Decimal("1800000"),
            sp2d_raw=self.sp2d,
        )
        DRPPUpload.objects.create(
            satker_code="",
            tahun=None,
            nomor_drpp="00042",
            nomor_drpp_norm="42",
            nomor_spm="00166T",
        )
        self.client.force_login(self.operator)

        response = self.client.get(
            reverse("documents:upload_kuitansi"),
            {"satker": "019937", "no_drpp": drpp_number},
        )

        self.assertContains(response, "DRPP ditemukan")
        self.assertEqual(response.context["drpp_context"]["transaction_count"], 1)
        self.assertIsNone(response.context["drpp_context"]["drpp_upload"])

    def test_manual_only_drpp_lookup_upload_and_checklist(self):
        drpp_number = "00044/DRPP/019937/2026"
        manual_tx = self.create_transaction(
            akun="521211",
            nomor_spm="00166T",
            no_drpp=drpp_number,
            no_kuitansi="00257/KW/019937/2026",
            nilai=Decimal("6500000"),
            sp2d_raw=self.sp2d,
        )
        other_tx = self.create_transaction(
            nomor_spm="00167T",
            no_drpp="00045/DRPP/019937/2026",
            no_kuitansi="00258/KW/019937/2026",
        )
        before = (
            manual_tx.helper,
            manual_tx.akun,
            manual_tx.nilai_bruto,
            manual_tx.nilai_netto,
            manual_tx.nomor_spm,
            manual_tx.sp2d_raw_id,
            manual_tx.status_detail,
            manual_tx.drpp_status,
        )
        self.client.force_login(self.operator)

        lookup_response = self.client.get(
            reverse("documents:upload_kuitansi"),
            {"satker": "019937", "no_drpp": drpp_number},
        )
        upload_response = self.upload_receipts(
            [self.receipt_file("manual-00044.pdf")],
            satker="019937",
            no_drpp=drpp_number,
            follow=True,
        )
        manual_tx.refresh_from_db()
        attachment = DRPPSupportingAttachment.objects.get(document_upload__original_filename="manual-00044.pdf")
        checklist_response = self.client.get(reverse("documents:checklist_detail", args=[manual_tx.pk]))
        other_checklist = self.client.get(reverse("documents:checklist_detail", args=[other_tx.pk]))

        self.assertContains(lookup_response, "DRPP ditemukan")
        self.assertEqual(lookup_response.context["drpp_context"]["transaction_count"], 1)
        self.assertIsNone(lookup_response.context["drpp_context"]["drpp_upload"])
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.filter(original_filename="manual-00044.pdf").count(), 1)
        self.assertIsNone(attachment.drpp_upload)
        self.assertEqual(attachment.satker_code, "019937")
        self.assertEqual(attachment.tahun, 2026)
        self.assertEqual(attachment.nomor_drpp_norm, "00044")
        self.assertEqual(attachment.nomor_drpp, drpp_number)
        # google_drive_url now contains a path when Drive is disabled (not empty)
        link = DocumentDriveLink.objects.get(nama_file="manual-00044.pdf")
        self.assertTrue(bool(link.google_drive_url))
        self.assertEqual(
            (
                manual_tx.helper,
                manual_tx.akun,
                manual_tx.nilai_bruto,
                manual_tx.nilai_netto,
                manual_tx.nomor_spm,
                manual_tx.sp2d_raw_id,
                manual_tx.status_detail,
                manual_tx.drpp_status,
            ),
            before,
        )
        self.assertContains(checklist_response, "manual-00044.pdf")
        self.assertNotContains(other_checklist, "manual-00044.pdf")

    def test_drpp_lookup_does_not_cross_satker_or_year(self):
        drpp_number = "00044/DRPP/019937/2026"
        self.create_transaction(
            nomor_spm="00166T",
            no_drpp=drpp_number,
            no_kuitansi="00257/KW/019937/2026",
        )
        self.create_transaction(
            satker_code="020000",
            nomor_spm="00166T",
            no_drpp="00044/DRPP/020000/2026",
            no_kuitansi="00257/KW/020000/2026",
        )
        self.create_transaction(
            nomor_spm="00166T",
            no_drpp="00044/DRPP/019937/2025",
            no_kuitansi="00257/KW/019937/2025",
            tanggal_spm=date(2025, 6, 15),
        )
        self.client.force_login(self.operator)

        own_response = self.client.get(
            reverse("documents:upload_kuitansi"),
            {"satker": "019937", "no_drpp": drpp_number},
        )
        other_response = self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "020000",
                "no_drpp": "00044/DRPP/020000/2026",
                "receipt_files": [self.receipt_file("forbidden.pdf")],
            },
        )

        self.assertEqual(own_response.context["drpp_context"]["transaction_count"], 1)
        self.assertEqual(other_response.status_code, 403)
        self.assertFalse(DocumentUpload.objects.filter(original_filename="forbidden.pdf").exists())
        self.assertFalse(DRPPSupportingAttachment.objects.exists())

    def test_missing_drpp_does_not_upload(self):
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "019937",
                "no_drpp": "99999/DRPP/019937/2026",
                "receipt_files": [self.receipt_file()],
            },
            follow=True,
        )

        self.assertContains(response, "DRPP tidak ditemukan pada data D_K.")
        self.assertEqual(DocumentUpload.objects.count(), 0)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 0)

    def test_cross_satker_upload_post_is_denied(self):
        other_tx = TransactionDetail.objects.create(
            satker_code="020000",
            akun="521111",
            nomor_spm="00166T",
            tanggal_spm=date(2026, 6, 15),
            no_drpp="00043/DRPP/020000/2026",
            nilai_bruto=1,
            nilai_netto=1,
        )
        other_drpp = DRPPUpload.objects.create(
            transaction_detail=other_tx,
            satker_code="020000",
            tahun=2026,
            nomor_drpp="00043/DRPP/020000/2026",
            nomor_drpp_norm=normalized_bukti_key("00043/DRPP/020000/2026"),
            nomor_spm="00166T",
        )
        self.client.force_login(self.operator)

        response = self.upload_receipts([self.receipt_file()], drpp_upload=other_drpp)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DocumentUpload.objects.count(), 0)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 0)

    def test_multifile_upload_creates_attachments_without_dk_mutation(self):
        before_count = TransactionDetail.objects.count()
        before = {
            self.tx1.pk: (self.tx1.helper, self.tx1.akun, self.tx1.nilai_bruto, self.tx1.nilai_netto, self.tx1.nomor_spm, self.tx1.sp2d_raw_id, self.tx1.status_detail),
            self.tx2.pk: (self.tx2.helper, self.tx2.akun, self.tx2.nilai_bruto, self.tx2.nilai_netto, self.tx2.nomor_spm, self.tx2.sp2d_raw_id, self.tx2.status_detail),
        }
        self.client.force_login(self.operator)

        response = self.upload_receipts(
            [
                self.receipt_file("kuitansi-00243.pdf", b"%PDF-1.4 receipt one"),
                self.receipt_file("kuitansi-00246.pdf", b"%PDF-1.4 receipt two"),
            ],
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.count(), 2)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 2)
        self.assertEqual(DocumentDriveLink.objects.filter(jenis_dokumen="Kuitansi").count(), 2)
        self.assertEqual(TransactionDetail.objects.count(), before_count)
        for transaction in (self.tx1, self.tx2):
            transaction.refresh_from_db()
            self.assertEqual(
                (transaction.helper, transaction.akun, transaction.nilai_bruto, transaction.nilai_netto, transaction.nomor_spm, transaction.sp2d_raw_id, transaction.status_detail),
                before[transaction.pk],
            )
        # google_drive_url now contains a path (local or Drive URL)
        self.assertTrue(
            DocumentDriveLink.objects.filter(jenis_dokumen="Kuitansi").exclude(google_drive_url="").exists()
        )
        self.assertFalse(DocumentDriveLink.objects.filter(transaction_detail__isnull=False).exists())

    def test_receipts_appear_once_in_archive_with_local_link(self):
        self.client.force_login(self.operator)
        self.upload_receipts([self.receipt_file("arsip-kuitansi.pdf")])

        response = self.client.get(reverse("documents:archive"))
        links = list(response.context["page_obj"].object_list)
        receipt_links = [link for link in links if link.jenis_dokumen == "Kuitansi"]
        attachment = DRPPSupportingAttachment.objects.get()
        download_url = reverse("documents:drpp_attachment_download", args=[attachment.pk])

        self.assertEqual(len(receipt_links), 1)
        # google_drive_url now contains a path when Drive is disabled (not empty)
        self.assertTrue(bool(receipt_links[0].google_drive_url))
        self.assertContains(response, "arsip-kuitansi.pdf")
        self.assertContains(response, download_url)
        self.assertContains(response, "Sinkronkan ke Drive")

    def test_checklist_shows_drpp_level_attachments_for_same_drpp_only(self):
        other_tx = TransactionDetail.objects.create(
            satker_code="019937",
            akun="521111",
            nomor_spm="00999T",
            tanggal_spm=date(2026, 6, 15),
            no_drpp="00999/DRPP/019937/2026",
            nilai_bruto=1,
            nilai_netto=1,
        )
        DRPPUpload.objects.create(
            transaction_detail=other_tx,
            satker_code="019937",
            tahun=2026,
            nomor_drpp="00999/DRPP/019937/2026",
            nomor_drpp_norm=normalized_bukti_key("00999/DRPP/019937/2026"),
            nomor_spm="00999T",
        )
        self.client.force_login(self.operator)
        self.upload_receipts([self.receipt_file("shared-kuitansi.pdf")])

        tx1_response = self.client.get(reverse("documents:checklist_detail", args=[self.tx1.pk]))
        tx2_response = self.client.get(reverse("documents:checklist_detail", args=[self.tx2.pk]))
        other_response = self.client.get(reverse("documents:checklist_detail", args=[other_tx.pk]))

        self.assertContains(tx1_response, "Kuitansi Pendukung DRPP")
        self.assertContains(tx1_response, "shared-kuitansi.pdf")
        self.assertContains(tx1_response, "1 file")
        self.assertContains(tx2_response, "shared-kuitansi.pdf")
        self.assertNotContains(other_response, "shared-kuitansi.pdf")

    def test_download_respects_satker_scope(self):
        self.client.force_login(self.operator)
        self.upload_receipts([self.receipt_file("download-kuitansi.pdf")])
        attachment = DRPPSupportingAttachment.objects.get()
        url = reverse("documents:drpp_attachment_download", args=[attachment.pk])

        own_response = self.client.get(url)
        self.client.force_login(self.other_operator)
        other_response = self.client.get(url)

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(other_response.status_code, 404)

    def test_exact_duplicate_content_is_skipped_in_same_drpp(self):
        self.client.force_login(self.operator)

        response = self.upload_receipts(
            [
                self.receipt_file("copy-a.pdf", b"same receipt content"),
                self.receipt_file("copy-b.pdf", b"same receipt content"),
            ],
            follow=True,
        )

        self.assertContains(response, "1 file duplikat konten dilewati.")
        self.assertEqual(DocumentUpload.objects.count(), 1)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 1)
        self.assertEqual(DocumentDriveLink.objects.count(), 1)
        # google_drive_url now contains a path when Drive is disabled (not empty)
        link = DocumentDriveLink.objects.get()
        self.assertTrue(bool(link.google_drive_url))


class DRPPSupportingAttachmentDriveTests(TestCase):
    """Tests for Google Drive integration with DRPP supporting attachments."""

    def setUp(self):
        self.media_tmp = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_tmp)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_tmp, True)

        self.admin = User.objects.create_superuser(
            username="drive-admin",
            password="password",
            email="",
        )
        self.operator = User.objects.create_user(username="operator-019937", password="password")
        self.operator.profile.role = Profile.Role.SATKER
        self.operator.profile.satker_code = "019937"
        self.operator.profile.save(update_fields=["role", "satker_code"])

        self.other_operator = User.objects.create_user(username="operator-020000", password="password")
        self.other_operator.profile.role = Profile.Role.SATKER
        self.other_operator.profile.satker_code = "020000"
        self.other_operator.profile.save(update_fields=["role", "satker_code"])

        self.no_drpp = "00042/DRPP/019937/2026"
        self.sp2d = SP2DRaw.objects.create(
            tahun=2026,
            satker_code="019937",
            satker_name="BPS Provinsi Sumatera Barat",
            no_sp2d="260100000030108",
            nomor_spm_extracted="00167T",
            nilai_sp2d=Decimal("1000000"),
            status=SP2DRaw.Status.COCOK,
        )
        self.tx = TransactionDetail.objects.create(
            sp2d_raw=self.sp2d,
            satker_code="019937",
            akun="522151",
            nomor_spm="00167T",
            tanggal_spm=date(2026, 7, 1),
            no_kuitansi="00247/KW/019937/2026",
            no_drpp=self.no_drpp,
            nilai_bruto=Decimal("1000000"),
            nilai_netto=Decimal("1000000"),
            status_detail=TransactionDetail.StatusDetail.LENGKAP,
            drpp_status=TransactionDetail.DRPPStatus.COCOK,
        )
        self.drpp_upload = DRPPUpload.objects.create(
            transaction_detail=self.tx,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            nomor_spm="00167T",
            match_status=DRPPUpload.MatchStatus.COCOK,
            uploaded_by=self.operator,
        )

    def receipt_file(self, name="kuitansi.pdf", content=b"%PDF-1.4 receipt"):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    @patch("apps.documents.views.drive_enabled", return_value=False)
    @patch("apps.documents.services.google_drive.drive_enabled", return_value=False)
    def test_drive_disabled_upload_still_succeeds(self, mock_gdrive_enabled, mock_view_enabled):
        """When Drive is disabled, upload still works and google_drive_url contains local archive path."""
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "019937",
                "no_drpp": self.no_drpp,
                "receipt_files": [self.receipt_file()],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.count(), 1)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 1)
        self.assertEqual(DocumentDriveLink.objects.count(), 1)

        # When Drive is disabled, google_drive_url contains local archive path
        link = DocumentDriveLink.objects.get()
        self.assertTrue(link.google_drive_url.startswith("/media/archive/"))
        self.assertContains(response, "1 file kuitansi pendukung tersimpan")

    @patch("apps.documents.services.google_drive.drive_enabled", return_value=False)
    def test_drive_disabled_archive_shows_sync_button(self, mock_drive_enabled):
        """When Drive is disabled, archive shows 'Sinkronkan ke Drive' for existing attachments."""
        self.client.force_login(self.operator)

        # Upload first
        self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "019937",
                "no_drpp": self.no_drpp,
                "receipt_files": [self.receipt_file("kuitansi-drive.pdf")],
            },
        )

        response = self.client.get(reverse("documents:archive"))
        # When Drive is disabled but attachment exists, show sync button
        self.assertContains(response, "Sinkronkan ke Drive")
        self.assertNotContains(response, "Buka Drive")

    @patch("apps.documents.views.drive_enabled", return_value=True)
    @patch("apps.documents.views.archive_file_link")
    def test_drive_enabled_upload_populates_google_drive_url(self, mock_archive_file_link, mock_drive_enabled):
        """When Drive is enabled with mocked service, google_drive_url gets populated."""
        self.client.force_login(self.operator)

        call_count = [0]

        def mock_archive_func(*args, **kwargs):
            call_count[0] += 1
            existing_link = kwargs.get("existing_link")
            if existing_link:
                existing_link.google_drive_url = "https://drive.google.com/file/d/mock-file-id-12345/view"
                existing_link.status = DocumentDriveLink.Status.AKTIF
                existing_link.save()
            return {
                "status": "uploaded",
                "file_id": "mock-file-id-12345",
                "web_view_link": "https://drive.google.com/file/d/mock-file-id-12345/view",
                "local_path": "",
                "mime_type": "application/pdf",
                "size": 20,
                "folder_id": "mock-folder-id",
                "error_message": "",
            }, existing_link, False

        mock_archive_file_link.side_effect = mock_archive_func

        response = self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "019937",
                "no_drpp": self.no_drpp,
                "receipt_files": [self.receipt_file("kuitansi-dengan-drive.pdf")],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.count(), 1)
        self.assertEqual(DocumentDriveLink.objects.count(), 1)
        # Assert Drive upload was called exactly once (no double upload)
        self.assertEqual(call_count[0], 1)

        link = DocumentDriveLink.objects.get()
        self.assertEqual(link.google_drive_url, "https://drive.google.com/file/d/mock-file-id-12345/view")
        self.assertContains(response, "1 file kuitansi pendukung tersimpan")
        self.assertContains(response, "Google Drive")

    def test_archive_shows_buka_drive_when_url_exists(self):
        """Archive page shows 'Buka Drive' link when google_drive_url is valid."""
        self.client.force_login(self.operator)

        # Create attachment with Drive URL
        doc_upload = DocumentUpload.objects.create(
            document_type="Kuitansi",
            original_filename="kuitansi-dengan-link.pdf",
            stored_filename="kuitansi-dengan-link.pdf",
            uploaded_by=self.operator,
        )
        doc_upload.file.save("kuitansi-dengan-link.pdf", SimpleUploadedFile("test.pdf", b"test"))

        link = DocumentDriveLink.objects.create(
            satker_code="019937",
            nomor_spm="00167T",
            no_drpp=self.no_drpp,
            jenis_dokumen="Kuitansi",
            nama_file="kuitansi-dengan-link.pdf",
            google_drive_url="https://drive.google.com/file/d/abc123/view",
            status=DocumentDriveLink.Status.AKTIF,
            created_by=self.operator,
        )
        attachment = DRPPSupportingAttachment.objects.create(
            drpp_upload=self.drpp_upload,
            document_upload=doc_upload,
            archive_link=link,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            uploaded_by=self.operator,
        )

        response = self.client.get(reverse("documents:archive"))

        self.assertContains(response, "Buka Drive")
        self.assertContains(response, "https://drive.google.com/file/d/abc123/view")
        self.assertNotContains(response, "Belum tersedia")

    def test_sync_endpoint_updates_existing_attachment(self):
        """Sync endpoint can upload existing local attachment to Drive."""
        self.client.force_login(self.operator)

        # Create local-only attachment
        doc_upload = DocumentUpload.objects.create(
            document_type="Kuitansi",
            original_filename="kuitansi-sync.pdf",
            stored_filename="kuitansi-sync.pdf",
            file_size=20,
            uploaded_by=self.operator,
        )
        doc_upload.file.save("kuitansi-sync.pdf", SimpleUploadedFile("test.pdf", b"sync test"))

        link = DocumentDriveLink.objects.create(
            satker_code="019937",
            nomor_spm="00167T",
            no_drpp=self.no_drpp,
            jenis_dokumen="Kuitansi",
            nama_file="kuitansi-sync.pdf",
            google_drive_url="",
            status=DocumentDriveLink.Status.PERLU_DICEK,
            created_by=self.operator,
        )
        attachment = DRPPSupportingAttachment.objects.create(
            drpp_upload=self.drpp_upload,
            document_upload=doc_upload,
            archive_link=link,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            uploaded_by=self.operator,
        )

        with patch("apps.documents.views.archive_file_link") as mock_archive:
            def mock_sync_func(*args, **kwargs):
                existing_link = kwargs.get("existing_link")
                if existing_link:
                    existing_link.google_drive_url = "https://drive.google.com/file/d/synced-file-id/view"
                    existing_link.status = DocumentDriveLink.Status.AKTIF
                    existing_link.save()
                return {
                    "status": "uploaded",
                    "file_id": "synced-file-id",
                    "web_view_link": "https://drive.google.com/file/d/synced-file-id/view",
                    "local_path": "",
                    "mime_type": "application/pdf",
                    "size": 20,
                    "folder_id": "folder-123",
                    "error_message": "",
                }, existing_link, False

            mock_archive.side_effect = mock_sync_func
            response = self.client.post(
                reverse("documents:sync_attachment_drive", args=[attachment.pk]),
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        link.refresh_from_db()
        self.assertEqual(link.google_drive_url, "https://drive.google.com/file/d/synced-file-id/view")
        self.assertContains(response, "berhasil diarsipkan ke Google Drive")

    def test_sync_idempotent_when_already_synced(self):
        """Sync endpoint is idempotent - won't re-upload if already synced."""
        self.client.force_login(self.operator)

        doc_upload = DocumentUpload.objects.create(
            document_type="Kuitansi",
            original_filename="already-synced.pdf",
            stored_filename="already-synced.pdf",
            file_size=20,
            uploaded_by=self.operator,
        )
        doc_upload.file.save("already-synced.pdf", SimpleUploadedFile("test.pdf", b"already synced"))

        link = DocumentDriveLink.objects.create(
            satker_code="019937",
            nomor_spm="00167T",
            no_drpp=self.no_drpp,
            jenis_dokumen="Kuitansi",
            nama_file="already-synced.pdf",
            google_drive_url="https://drive.google.com/file/d/existing-id/view",
            status=DocumentDriveLink.Status.AKTIF,
            created_by=self.operator,
        )
        attachment = DRPPSupportingAttachment.objects.create(
            drpp_upload=self.drpp_upload,
            document_upload=doc_upload,
            archive_link=link,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            uploaded_by=self.operator,
        )

        response = self.client.post(
            reverse("documents:sync_attachment_drive", args=[attachment.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sudah tersinkron")

    def test_sync_respects_satker_permission(self):
        """Operator Satker A cannot sync attachment from Satker B."""
        self.client.force_login(self.operator)

        doc_upload = DocumentUpload.objects.create(
            document_type="Kuitansi",
            original_filename="other-satker.pdf",
            stored_filename="other-satker.pdf",
            file_size=20,
            uploaded_by=self.other_operator,
        )
        doc_upload.file.save("other-satker.pdf", SimpleUploadedFile("test.pdf", b"other satker"))

        link = DocumentDriveLink.objects.create(
            satker_code="020000",
            nomor_spm="00200T",
            no_drpp="00042/DRPP/020000/2026",
            jenis_dokumen="Kuitansi",
            nama_file="other-satker.pdf",
            google_drive_url="",
            status=DocumentDriveLink.Status.PERLU_DICEK,
            created_by=self.other_operator,
        )
        attachment = DRPPSupportingAttachment.objects.create(
            document_upload=doc_upload,
            archive_link=link,
            satker_code="020000",
            tahun=2026,
            nomor_drpp="00042/DRPP/020000/2026",
            nomor_drpp_norm="00042/DRPP/020000/2026",
            uploaded_by=self.other_operator,
        )

        response = self.client.post(
            reverse("documents:sync_attachment_drive", args=[attachment.pk]),
        )

        self.assertEqual(response.status_code, 404)

    def test_checklist_shows_drive_link_for_attachment(self):
        """Checklist detail shows Drive link when google_drive_url exists."""
        self.client.force_login(self.operator)

        doc_upload = DocumentUpload.objects.create(
            document_type="Kuitansi",
            original_filename="checklist-drive.pdf",
            stored_filename="checklist-drive.pdf",
            file_size=20,
            uploaded_by=self.operator,
        )
        doc_upload.file.save("checklist-drive.pdf", SimpleUploadedFile("test.pdf", b"checklist"))

        link = DocumentDriveLink.objects.create(
            satker_code="019937",
            nomor_spm="00167T",
            no_drpp=self.no_drpp,
            jenis_dokumen="Kuitansi",
            nama_file="checklist-drive.pdf",
            google_drive_url="https://drive.google.com/file/d/checklist-id/view",
            status=DocumentDriveLink.Status.AKTIF,
            created_by=self.operator,
        )
        DRPPSupportingAttachment.objects.create(
            drpp_upload=self.drpp_upload,
            document_upload=doc_upload,
            archive_link=link,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            uploaded_by=self.operator,
        )

        response = self.client.get(reverse("documents:checklist_detail", args=[self.tx.pk]))

        self.assertContains(response, "Buka Drive")
        self.assertContains(response, "https://drive.google.com/file/d/checklist-id/view")

    def test_checklist_shows_sync_button_when_drive_url_missing(self):
        """Checklist shows 'Sinkronkan ke Drive' button when google_drive_url is empty."""
        self.client.force_login(self.operator)

        doc_upload = DocumentUpload.objects.create(
            document_type="Kuitansi",
            original_filename="belum-sync.pdf",
            stored_filename="belum-sync.pdf",
            file_size=20,
            uploaded_by=self.operator,
        )
        doc_upload.file.save("belum-sync.pdf", SimpleUploadedFile("test.pdf", b"belum"))

        link = DocumentDriveLink.objects.create(
            satker_code="019937",
            nomor_spm="00167T",
            no_drpp=self.no_drpp,
            jenis_dokumen="Kuitansi",
            nama_file="belum-sync.pdf",
            google_drive_url="",
            status=DocumentDriveLink.Status.PERLU_DICEK,
            created_by=self.operator,
        )
        DRPPSupportingAttachment.objects.create(
            drpp_upload=self.drpp_upload,
            document_upload=doc_upload,
            archive_link=link,
            satker_code="019937",
            tahun=2026,
            nomor_drpp=self.no_drpp,
            nomor_drpp_norm=normalized_bukti_key(self.no_drpp),
            uploaded_by=self.operator,
        )

        response = self.client.get(reverse("documents:checklist_detail", args=[self.tx.pk]))

        self.assertContains(response, "Sinkronkan ke Drive")
        self.assertNotContains(response, "Buka Drive")

    def test_upload_with_drive_failure_preserves_local_file(self):
        """When Drive upload fails, local file and attachment are preserved."""
        self.client.force_login(self.operator)

        mock_drive_result = {
            "status": "failed",
            "file_id": "",
            "web_view_link": "",
            "local_path": "",
            "mime_type": "application/pdf",
            "size": 20,
            "folder_id": None,
            "error_message": "Connection timeout",
        }

        with patch("apps.documents.views.archive_file_link") as mock_archive:
            mock_archive.return_value = (mock_drive_result, None, False)
            response = self.client.post(
                reverse("documents:upload_kuitansi"),
                {
                    "action": "upload_receipts",
                    "satker": "019937",
                    "no_drpp": self.no_drpp,
                    "receipt_files": [self.receipt_file("kuitansi-gagal.pdf")],
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.count(), 1)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 1)
        self.assertEqual(DocumentDriveLink.objects.count(), 1)

        link = DocumentDriveLink.objects.get()
        self.assertEqual(link.google_drive_url, "")
        self.assertContains(response, "1 file kuitansi pendukung tersimpan")

    @patch("apps.documents.services.google_drive.drive_enabled", return_value=False)
    def test_multi_file_upload_all_succeed_without_drive(self, mock_drive_enabled):
        """When Drive disabled, uploading multiple files all succeed with local archive paths."""
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "019937",
                "no_drpp": self.no_drpp,
                "receipt_files": [
                    self.receipt_file("kuitansi-1.pdf", b"receipt one"),
                    self.receipt_file("kuitansi-2.pdf", b"receipt two"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.count(), 2)
        self.assertEqual(DRPPSupportingAttachment.objects.count(), 2)
        self.assertEqual(DocumentDriveLink.objects.count(), 2)

        # When Drive is disabled, google_drive_url contains local archive paths
        for link in DocumentDriveLink.objects.all():
            self.assertTrue(link.google_drive_url.startswith("/media/archive/"))

    @patch("apps.documents.views.drive_enabled", return_value=True)
    @patch("apps.documents.views.archive_file_link")
    def test_multi_file_upload_all_get_drive_urls(self, mock_archive_file_link, mock_drive_enabled):
        """When Drive enabled, all files get Drive URLs (no duplicates)."""
        self.client.force_login(self.operator)

        file_counter = [0]

        def mock_archive_func(*args, **kwargs):
            file_counter[0] += 1
            existing_link = kwargs.get("existing_link")
            if existing_link:
                existing_link.google_drive_url = f"https://drive.google.com/file/d/file-id-{file_counter[0]}/view"
                existing_link.status = DocumentDriveLink.Status.AKTIF
                existing_link.save()
            return {
                "status": "uploaded",
                "file_id": f"file-id-{file_counter[0]}",
                "web_view_link": f"https://drive.google.com/file/d/file-id-{file_counter[0]}/view",
                "local_path": "",
                "mime_type": "application/pdf",
                "size": 20,
                "folder_id": "folder-123",
                "error_message": "",
            }, existing_link, False

        mock_archive_file_link.side_effect = mock_archive_func

        response = self.client.post(
            reverse("documents:upload_kuitansi"),
            {
                "action": "upload_receipts",
                "satker": "019937",
                "no_drpp": self.no_drpp,
                "receipt_files": [
                    self.receipt_file("kuitansi-multi-1.pdf", b"multi one"),
                    self.receipt_file("kuitansi-multi-2.pdf", b"multi two"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DocumentUpload.objects.count(), 2)
        self.assertEqual(DocumentDriveLink.objects.count(), 2)

        # Assert Drive upload was called exactly once per file (no double upload)
        self.assertEqual(file_counter[0], 2)

        for link in DocumentDriveLink.objects.all():
            self.assertTrue(link.google_drive_url.startswith("https://drive.google.com/"))

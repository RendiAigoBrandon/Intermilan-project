from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Profile
from apps.dk.models import TransactionDetail

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
        labels = (
            "Home",
            "Dashboard",
            "D_K",
            "Upload SP2D",
            "Upload DRPP dan Kuitansi",
            "Arsip",
            "Review",
            "Monitoring",
            "Master Akun",
            "Peraturan",
            "Template",
            "Panduan",
            "Laporan",
            "Logout",
        )
        positions = [sidebar.index(f">{label}<") for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Akun Keuangan", sidebar)
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

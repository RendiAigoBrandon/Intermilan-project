from django.contrib.auth import get_user_model
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from apps.accounts.access import can_access_audit_data, can_edit_transaction, can_upload_document, is_admin, is_operator_satker, is_viewer
from apps.accounts.models import Profile
from apps.core.models import MonitoringSummary
from apps.core.monitoring_summary import refresh_monitoring_summary
from apps.dk.models import TransactionDetail
from apps.dk.services import requires_drpp
from apps.sp2d.models import SP2DRaw


class CoreAccessTests(TestCase):
    def make_user(self, username, role=Profile.Role.VIEWER, satker_code="", is_superuser=False):
        user = get_user_model().objects.create_user(username=username, password="strong-password", is_superuser=is_superuser)
        profile = user.profile
        profile.role = role
        profile.satker_code = satker_code
        profile.save()
        return user

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_authenticated_user_can_open_dashboard(self):
        user = self.make_user("tester")
        self.client.force_login(user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INTERMILAN")

    def test_dashboard_admin_chart_uses_satker_axis(self):
        admin = self.make_user("admin_dashboard_scope", Profile.Role.ADMIN_PUSAT)
        SP2DRaw.objects.create(satker_code="1300", nomor_spm_extracted="SPM1300")
        SP2DRaw.objects.create(satker_code="1301", nomor_spm_extracted="SPM1301")
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="DASH1300", bulan_sp2d=1, nilai_netto=100)
        TransactionDetail.objects.create(satker_code="1301", akun="522111", nomor_spm="DASH1301", bulan_sp2d=1, nilai_netto=200)
        self.client.force_login(admin)

        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scope: Semua Satker")
        self.assertContains(response, "Desember")
        self.assertContains(response, 'data-chart-satker="bps1300"')
        self.assertContains(response, 'data-chart-satker="bps1301"')
        self.assertContains(response, "FA16 bulan ini")
        self.assertContains(response, "DASH1300")
        self.assertContains(response, "DASH1301")

    def test_import_monitoring_summary_creates_baseline(self):
        with TemporaryDirectory() as tmpdir:
            workbook_path = Path(tmpdir) / "INTERMILAN.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "Monitoring_Combine"
            ws.append([
                "No",
                "BPS Prov/Kab/Kota",
                "Bulan SP2D",
                "Realisasi FA 16 Detil Bulan ini (di isi satker)",
                "Realisasi Intermilan Bulan ini",
                "Realisasi Intermilan s.d Bulan Ini",
                "Persentase Realisasi Intermilan terhadap FA 16 Detil (Max 100%)",
                "Persentase Kelengkapan Dokumen",
                "Persentase SPJ yang sudah di Upload",
                "Persentase dokumen sudah di arsipkan",
                "Deadline",
                "Status",
                "% Completed",
                "BAR",
                "TA",
            ])
            ws.append([1, "bps1300", "Januari", 1000, 500, 500, 0.5, 0.25, 1, 0, "2026-02-25", "In Progress", 0.5, "50%", 2026])
            wb.save(workbook_path)

            call_command("import_monitoring_summary", "--path", str(workbook_path), "--commit", verbosity=0)

        summary = MonitoringSummary.objects.get(satker_code="1300", bulan_number=1, tahun=2026)
        self.assertEqual(summary.fa16_bulan_ini, Decimal("1000.00"))
        self.assertEqual(summary.persen_realisasi, Decimal("50.00"))
        self.assertEqual(summary.source, MonitoringSummary.Source.EXCEL_SEED)

    def test_refresh_monitoring_summary_updates_intermilan_without_changing_fa16(self):
        summary = MonitoringSummary.objects.create(
            satker_code="1300",
            satker_label="bps1300",
            bulan="Januari",
            bulan_number=1,
            tahun=2026,
            fa16_bulan_ini=1000,
            intermilan_bulan_ini=100,
            persen_realisasi=10,
        )
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="REFRESH001", bulan_sp2d=1, nilai_netto=250)

        refreshed = refresh_monitoring_summary(tahun=2026, bulan=1, satker_code="1300")

        self.assertEqual(refreshed, 1)
        summary.refresh_from_db()
        self.assertEqual(summary.fa16_bulan_ini, Decimal("1000.00"))
        self.assertEqual(summary.intermilan_bulan_ini, Decimal("250.00"))
        self.assertEqual(summary.intermilan_sd_bulan_ini, Decimal("250.00"))
        self.assertEqual(summary.persen_realisasi, Decimal("25.00"))
        self.assertEqual(summary.source, MonitoringSummary.Source.MIXED)
        self.assertIsNotNone(summary.last_refreshed_at)

    def test_refresh_monitoring_summary_does_not_create_duplicate_rows(self):
        MonitoringSummary.objects.create(
            satker_code="1300",
            satker_label="bps1300",
            bulan="Januari",
            bulan_number=1,
            tahun=2026,
            fa16_bulan_ini=1000,
        )
        refresh_monitoring_summary(tahun=2026, bulan=1, satker_code="1300")
        refresh_monitoring_summary(tahun=2026, bulan=1, satker_code="1300")

        self.assertEqual(MonitoringSummary.objects.filter(satker_code="1300", bulan_number=1, tahun=2026).count(), 1)

    def test_dashboard_reads_monitoring_summary_after_refresh(self):
        admin = self.make_user("admin_dashboard_summary", Profile.Role.ADMIN_PUSAT)
        summary = MonitoringSummary.objects.create(
            satker_code="1300",
            satker_label="bps1300",
            bulan="Januari",
            bulan_number=1,
            tahun=2026,
            fa16_bulan_ini=1000,
            intermilan_bulan_ini=100,
            persen_realisasi=10,
        )
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="SUMMARY001", bulan_sp2d=1, nilai_netto=400)
        refresh_monitoring_summary(tahun=2026, bulan=1, satker_code="1300")
        summary.refresh_from_db()
        self.client.force_login(admin)

        response = self.client.get(reverse("core:dashboard"), {"tahun": "2026", "bulan": "1"})
        self.assertContains(response, "MonitoringSummary")
        self.assertContains(response, "Terakhir diperbarui")
        self.assertContains(response, "FA16 bulan ini: Rp 1.000")
        self.assertContains(response, "Intermilan bulan ini: Rp 400")
        self.assertContains(response, "40,00%")

    def test_monitoring_page_reads_monitoring_summary_and_filters(self):
        admin = self.make_user("admin_monitoring_summary", Profile.Role.ADMIN_PUSAT)
        MonitoringSummary.objects.create(
            satker_code="1300",
            satker_label="bps1300",
            bulan="Juni",
            bulan_number=6,
            tahun=2026,
            fa16_bulan_ini=1000,
            intermilan_bulan_ini=800,
            intermilan_sd_bulan_ini=2000,
            persen_realisasi=80,
            status="In Progress",
            percent_completed=75,
        )
        MonitoringSummary.objects.create(
            satker_code="1301",
            satker_label="bps1301",
            bulan="Juli",
            bulan_number=7,
            tahun=2026,
            status="Belum realisasi",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("core:monitoring"), {"tahun": "2026", "bulan": "6", "satker": "1300", "status": "In Progress"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["bps"], "bps1300")
        self.assertContains(response, "MonitoringSummary")
        self.assertContains(response, "bps1300")
        self.assertContains(response, "1.000")
        self.assertContains(response, "800")
        self.assertContains(response, "80,00%")

        search_response = self.client.get(reverse("core:monitoring"), {"q": "belum realisasi"})
        self.assertEqual(len(search_response.context["rows"]), 1)
        self.assertEqual(search_response.context["rows"][0]["bps"], "bps1301")
        self.assertContains(search_response, "bps1301")

    @override_settings(DEBUG=True)
    def test_create_dev_users_all_satker_uses_active_satkers(self):
        MonitoringSummary.objects.create(satker_code="1300", satker_label="bps1300", bulan="Januari", bulan_number=1, tahun=2026)
        MonitoringSummary.objects.create(satker_code="1377", satker_label="bps1377", bulan="Januari", bulan_number=1, tahun=2026)

        call_command("create_dev_users", "--password", "test-password", "--all-satker", verbosity=0)

        User = get_user_model()
        self.assertTrue(User.objects.filter(username="operator_1300", profile__role=Profile.Role.SATKER, profile__satker_code="1300").exists())
        self.assertTrue(User.objects.filter(username="operator_1377", profile__role=Profile.Role.SATKER, profile__satker_code="1377").exists())
        self.assertTrue(User.objects.filter(username="admin", profile__role=Profile.Role.ADMIN_PUSAT).exists())
        self.assertTrue(User.objects.filter(username="viewer", profile__role=Profile.Role.VIEWER).exists())

    def test_dashboard_operator_cards_are_satker_but_chart_is_all_satker_read_only(self):
        operator = self.make_user("operator_dashboard_scope", Profile.Role.SATKER, "1300")
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="OP1300", bulan_sp2d=1, nilai_netto=100)
        TransactionDetail.objects.create(satker_code="1301", akun="522111", nomor_spm="OP1301", bulan_sp2d=1, nilai_netto=200)
        self.client.force_login(operator)

        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "Scope: Satker 1300")
        self.assertContains(response, "Scope Chart: Semua Satker (Read Only)")
        self.assertContains(response, 'data-chart-satker="bps1301"')
        self.assertContains(response, "OP1300")
        self.assertNotContains(response, "OP1301")

    def test_dashboard_month_filter_changes_visible_rows(self):
        admin = self.make_user("admin_dashboard_month", Profile.Role.ADMIN_PUSAT)
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="JAN-DASH", bulan_sp2d=1, nilai_netto=100)
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="FEB-DASH", bulan_sp2d=2, nilai_netto=200)
        self.client.force_login(admin)

        response = self.client.get(reverse("core:dashboard"), {"tahun": "2026", "bulan": "2"})
        self.assertContains(response, "Bulan Fokus")
        self.assertContains(response, "Februari")
        self.assertContains(response, "FEB-DASH")
        self.assertNotContains(response, "JAN-DASH")

    def test_role_helpers(self):
        admin = self.make_user("admin_user", Profile.Role.ADMIN_PUSAT)
        operator = self.make_user("operator_1300", Profile.Role.SATKER, "1300")
        viewer = self.make_user("viewer_user", Profile.Role.VIEWER)
        transaction_1300 = TransactionDetail.objects.create(satker_code="1300", akun="522111")
        transaction_1301 = TransactionDetail.objects.create(satker_code="1301", akun="522111")

        self.assertTrue(is_admin(admin))
        self.assertTrue(is_operator_satker(operator))
        self.assertTrue(is_viewer(viewer))
        self.assertTrue(can_edit_transaction(operator, transaction_1300))
        self.assertFalse(can_edit_transaction(operator, transaction_1301))
        self.assertFalse(can_upload_document(viewer))
        self.assertTrue(can_access_audit_data(admin))
        self.assertFalse(can_access_audit_data(operator))

    def test_audit_data_admin_only(self):
        admin = self.make_user("admin_audit", Profile.Role.ADMIN_PUSAT)
        operator = self.make_user("operator_audit", Profile.Role.SATKER, "1300")
        viewer = self.make_user("viewer_audit", Profile.Role.VIEWER)

        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("core:audit_data")).status_code, 200)

        self.client.force_login(operator)
        self.assertEqual(self.client.get(reverse("core:audit_data")).status_code, 403)

        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("core:audit_data")).status_code, 403)

    def test_documents_list_and_detail_are_separate(self):
        user = self.make_user("doc_admin", Profile.Role.ADMIN_PUSAT)
        transaction = TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="00999T")
        self.client.force_login(user)

        list_response = self.client.get(reverse("documents:checklist"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Buka D_K")
        self.assertNotContains(list_response, "00074T")

        detail_response = self.client.get(reverse("documents:checklist_detail", args=[transaction.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "00999T")

    def test_requires_drpp_helper(self):
        ls_transaction = TransactionDetail.objects.create(satker_code="1300", akun="522111", cara_pembayaran="LS Non Kontraktual")
        drpp_number_transaction = TransactionDetail.objects.create(satker_code="1300", akun="522111", no_drpp="001/DRPP")
        gup_transaction = TransactionDetail.objects.create(satker_code="1300", akun="522111", cara_pembayaran="GUP")

        self.assertFalse(requires_drpp(ls_transaction))
        self.assertTrue(requires_drpp(drpp_number_transaction))
        self.assertTrue(requires_drpp(gup_transaction))

    def test_dk_search_filters_result_content(self):
        admin = self.make_user("admin_filter_dk", Profile.Role.ADMIN_PUSAT)
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="FIND123", deskripsi="target row")
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="OTHER456", deskripsi="other row")
        self.client.force_login(admin)

        response = self.client.get(reverse("dk:list"), {"q": "FIND123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FIND123")
        self.assertNotContains(response, "OTHER456")

        pembebanan_row = TransactionDetail.objects.create(
            satker_code="1300", akun="522111", nomor_spm="PEMB001", pembebanan="2886.TEST.SEARCH"
        )
        pembebanan_response = self.client.get(reverse("dk:list"), {"q": "2886.TEST.SEARCH"})
        self.assertContains(pembebanan_response, pembebanan_row.nomor_spm)
        self.assertNotContains(pembebanan_response, "OTHER456")

    def test_dk_month_and_satker_filters_result_content(self):
        admin = self.make_user("admin_filter_month", Profile.Role.ADMIN_PUSAT)
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="JAN1300", bulan_sp2d=1)
        TransactionDetail.objects.create(satker_code="1301", akun="522111", nomor_spm="FEB1301", bulan_sp2d=2)
        self.client.force_login(admin)

        month_response = self.client.get(reverse("dk:list"), {"bulan": "2"})
        self.assertContains(month_response, "FEB1301")
        self.assertNotContains(month_response, "JAN1300")

        satker_response = self.client.get(reverse("dk:list"), {"satker": "1301"})
        self.assertContains(satker_response, "FEB1301")
        self.assertNotContains(satker_response, "JAN1300")

    def test_dk_pagination_keeps_full_filtered_total_not_legacy_slice(self):
        admin = self.make_user("admin_dk_pagination", Profile.Role.ADMIN_PUSAT)
        for index in range(55):
            TransactionDetail.objects.create(
                satker_code="1300",
                akun="522111",
                nomor_spm=f"PAGE{index:03d}",
                bulan_sp2d=1,
            )
        self.client.force_login(admin)

        response = self.client.get(reverse("dk:list"), {"page_size": "20"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["paginator"].count, 55)
        self.assertEqual(response.context["paginator"].num_pages, 3)
        self.assertEqual(len(response.context["rows"]), 20)
        self.assertContains(response, "Menampilkan 1-20 dari 55 data")
        self.assertEqual(response.context["base_querystring"], "page_size=20")
        self.assertContains(response, "page=2")

    def test_dk_page_two_has_different_rows_and_query_params_survive(self):
        admin = self.make_user("admin_dk_page_two", Profile.Role.ADMIN_PUSAT)
        for index in range(25):
            TransactionDetail.objects.create(
                satker_code="1300",
                akun="522111",
                nomor_spm=f"KEEP{index:03d}",
                bulan_sp2d=1,
            )
        self.client.force_login(admin)

        page_one = self.client.get(reverse("dk:list"), {"q": "KEEP", "page_size": "20"})
        page_two = self.client.get(reverse("dk:list"), {"q": "KEEP", "page_size": "20", "page": "2"})

        page_one_spms = [row.nomor_spm for row in page_one.context["rows"]]
        page_two_spms = [row.nomor_spm for row in page_two.context["rows"]]
        self.assertNotEqual(page_one_spms, page_two_spms)
        self.assertIn("KEEP000", page_one_spms)
        self.assertIn("KEEP020", page_two_spms)
        self.assertEqual(page_one.context["base_querystring"], "q=KEEP&page_size=20")
        self.assertContains(page_one, "page=2")

    def test_dashboard_table_uses_excel_dashboard_labels_and_dk_preview(self):
        admin = self.make_user("admin_dashboard_table_labels", Profile.Role.ADMIN_PUSAT)
        MonitoringSummary.objects.create(
            satker_code="1300",
            satker_label="bps1300",
            bulan="Januari",
            bulan_number=1,
            tahun=2026,
            fa16_bulan_ini=1000,
            intermilan_bulan_ini=900,
            intermilan_sd_bulan_ini=900,
            persen_realisasi=90,
            status="In Progress",
            percent_completed=75,
            bar="75%",
        )
        TransactionDetail.objects.create(
            satker_code="1300",
            akun="522111",
            nomor_spm="DASH-DK",
            bulan_sp2d=1,
            cara_pembayaran="LS",
            jenis_spm="GAJI",
            nilai_bruto=1000,
            pembebanan="2886.TEST",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("core:dashboard"), {"tahun": "2026", "bulan": "1"})

        self.assertEqual(response.context["summary_source_label"], "MonitoringSummary")
        self.assertEqual(response.context["dashboard_table_source"], "D_K preview sesuai kolom Dashboard Excel")
        self.assertEqual(response.context["dashboard_columns"][0], "Bulan SP2D")
        self.assertContains(response, "No. Kuitansi (Hanya untuk dana UP/PTUP)/No. SPM")
        self.assertContains(response, "Bulan SP2D / Bulan Fokus")
        self.assertContains(response, "Jenis SPM")
        self.assertContains(response, "DASH-DK")
        self.assertNotContains(response, "Preview Detail Keuangan")

    def test_sp2d_search_invoice_filters_result_content(self):
        admin = self.make_user("admin_filter_sp2d", Profile.Role.ADMIN_PUSAT)
        SP2DRaw.objects.create(satker_code="1300", satker_name="Satker A", nomor_invoice="INV-FIND", no_sp2d="SP2D-FIND")
        SP2DRaw.objects.create(satker_code="1300", satker_name="Satker A", nomor_invoice="INV-OTHER", no_sp2d="SP2D-OTHER")
        self.client.force_login(admin)

        response = self.client.get(reverse("sp2d:list"), {"q": "INV-FIND"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INV-FIND")
        self.assertNotContains(response, "INV-OTHER")

    def test_monitoring_status_filter_changes_result_content(self):
        admin = self.make_user("admin_filter_monitoring", Profile.Role.ADMIN_PUSAT)
        TransactionDetail.objects.create(satker_code="1300", akun="522111", nomor_spm="MON1300", bulan_sp2d=1, nilai_netto=100)
        self.client.force_login(admin)

        response = self.client.get(reverse("core:monitoring"), {"status": "in_progress"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "bps1300")

        empty_response = self.client.get(reverse("core:monitoring"), {"status": "done"})
        self.assertEqual(empty_response.status_code, 200)
        self.assertNotContains(empty_response, "bps1300")

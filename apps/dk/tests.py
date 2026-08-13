from django.test import TestCase, Client
from django.urls import NoReverseMatch, reverse
from django.contrib.auth import get_user_model
from apps.dk.models import TransactionDetail, MasterAkun, TransactionChangeLog
from apps.accounts.models import Profile
from apps.sp2d.models import SP2DRaw

User = get_user_model()

class DKTests(TestCase):
    def setUp(self):
        # Admin User
        self.user = User.objects.create_user(username="testuser", password="password", is_superuser=True)
        Profile.objects.filter(user=self.user).update(role=Profile.Role.ADMIN_PUSAT)
        
        # Operator Satker
        self.operator = User.objects.create_user(username="op", password="password")
        Profile.objects.filter(user=self.operator).update(
            role=Profile.Role.SATKER,
            satker_code="SAT1",
            satker_name="Satker Satu",
        )
        
        # Viewer
        self.viewer = User.objects.create_user(username="view", password="password")
        Profile.objects.filter(user=self.viewer).update(role=Profile.Role.VIEWER)

        self.client = Client()
        self.client.login(username="testuser", password="password")
        
        self.akun = MasterAkun.objects.create(kode="12345", nama_akun="Test Akun", is_active=True)
        self.helper_akun = MasterAkun.objects.create(kode="521115", nama_akun="Belanja Honor", is_active=True)
        self.sp2d_sat1 = SP2DRaw.objects.create(
            satker_code="SAT1",
            satker_name="Satker Satu",
            no_sp2d="SP2D-SAT1",
            nomor_spm_extracted="SPM001",
            tahun=2026,
        )
        self.sp2d_sat2 = SP2DRaw.objects.create(
            satker_code="SAT2",
            satker_name="Satker Dua",
            no_sp2d="SP2D-SAT2",
            nomor_spm_extracted="SPM002",
            tahun=2026,
        )
        self.transaction = TransactionDetail.objects.create(
            satker_code="SAT1",
            akun="12345",
            bulan_sp2d="1",
            cara_pembayaran="LS",
            nomor_spm="SPM001",
            tanggal_spm="2026-01-01",
            jenis_spm="Gaji",
            no_kuitansi="KUIT001",
            no_drpp="DRPP001",
            deskripsi="Test Desc",
            nilai_bruto=1000,
            nilai_netto=900,
            pph21=100,
            created_by=self.user,
            status_detail=TransactionDetail.StatusDetail.DRAFT
        )

    def valid_transaction_payload(self, **overrides):
        data = {
            'satker_code': 'SAT1',
            'sp2d_raw_id': '',
            'akun': '12345',
            'bulan_sp2d': '2',
            'cara_pembayaran': 'LS',
            'nomor_spm': 'SPM-VALID',
            'tanggal_spm': '2026-02-01',
            'jenis_spm': 'Gaji',
            'no_kuitansi': 'KUIT-VALID',
            'no_drpp': 'DRPP-VALID',
            'deskripsi': 'Test',
            'nilai_bruto': 2000,
            'nilai_netto': 1800,
            'pembebanan': '',
            'fp': '',
            'pph21': 200,
        }
        data.update(overrides)
        return data

    def test_helper_property(self):
        self.assertEqual(self.transaction.helper, "12345KUIT001")

    def test_create_form_renders_manual_business_structure(self):
        response = self.client.get(reverse("dk:transaction_create"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        expected_labels = [
            "Satker",
            "Nama Satker",
            "No SP2D",
            "Helper",
            "Akun",
            "Bulan SP2D",
            "Cara Pembayaran",
            "Nomor SPM",
            "Tanggal SPM",
            "Jenis SPM",
            "No. Kuitansi",
            "No. DRPP",
            "Deskripsi",
            "Nilai Bruto",
            "Nilai Netto",
            "Pembebanan",
            "FP",
            "PPh21",
        ]

        last_index = -1
        for label in expected_labels:
            with self.subTest(label=label):
                index = html.find(f">{label}", last_index + 1)
                self.assertGreater(index, last_index)
                last_index = index
        self.assertNotIn("Sp2d raw id", html)

    def test_create_transaction_with_sp2d_links_and_displays_business_labels(self):
        sp2d = SP2DRaw.objects.create(
            satker_code="019937",
            satker_name="BPS Provinsi Sumatera Barat",
            no_sp2d="260100000019075",
            nomor_spm_extracted="00100T",
            tanggal_invoice="2026-02-01",
            bulan_sp2d=2,
            jenis_spm="UP/TUP",
            deskripsi="SP2D untuk manual D_K",
            tahun=2026,
            nilai_spm=2000,
            nilai_sp2d=1800,
            potongan=200,
        )
        response = self.client.post(
            reverse('dk:transaction_create'),
            self.valid_transaction_payload(
                satker_code="019937",
                sp2d_raw_id=str(sp2d.id),
                akun="521115",
                nomor_spm="SPM-LINKED",
                no_kuitansi="00166/KW/019937/2026",
                cara_pembayaran="UP/TUP",
                jenis_spm="UP/TUP",
            ),
        )
        self.assertEqual(response.status_code, 302)

        transaction = TransactionDetail.objects.get(nomor_spm="SPM-LINKED")
        self.assertEqual(transaction.sp2d_raw_id, sp2d.id)
        self.assertEqual(transaction.satker_code, "019937")

        list_response = self.client.get(reverse("dk:transaction_list"), {"q": "SPM-LINKED"})
        self.assertContains(list_response, "260100000019075")
        self.assertContains(list_response, "BPS Provinsi Sumatera Barat")

    def test_helper_is_derived_and_not_stored(self):
        transaction = TransactionDetail.objects.create(
            satker_code="019937",
            akun="521115",
            no_kuitansi="00166/KW/019937/2026",
            nilai_bruto=0,
            nilai_netto=0,
            pph21=0,
            created_by=self.user,
        )
        self.assertEqual(transaction.helper, "52111500166/KW/019937/2026")
        self.assertNotIn("helper", [field.name for field in TransactionDetail._meta.fields])

    def test_operator_cannot_link_sp2d_from_another_satker(self):
        operator = User.objects.create_user(username="op_019937", password="password")
        Profile.objects.filter(user=operator).update(
            role=Profile.Role.SATKER,
            satker_code="019937",
            satker_name="BPS Provinsi Sumatera Barat",
        )
        other_sp2d = SP2DRaw.objects.create(
            satker_code="428041",
            satker_name="BPS Kabupaten Agam",
            no_sp2d="SP2D-AGAM",
            tahun=2026,
        )

        self.client.login(username="op_019937", password="password")
        response = self.client.post(
            reverse('dk:transaction_create'),
            self.valid_transaction_payload(
                satker_code="428041",
                sp2d_raw_id=str(other_sp2d.id),
                akun="521115",
                nomor_spm="SPM-BLOCKED",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TransactionDetail.objects.filter(nomor_spm="SPM-BLOCKED").exists())
        self.assertContains(response, "SP2D tidak ditemukan atau beda satker.")

    def test_manual_create_numeric_fields_require_values_but_accept_zero(self):
        blank_response = self.client.post(
            reverse('dk:transaction_create'),
            self.valid_transaction_payload(
                nomor_spm="SPM-BLANK-NUMERIC",
                nilai_bruto="",
                nilai_netto="",
                pph21="",
            ),
        )
        self.assertEqual(blank_response.status_code, 200)
        self.assertFalse(TransactionDetail.objects.filter(nomor_spm="SPM-BLANK-NUMERIC").exists())
        expected_error = "Isi 0 hanya jika nilai dokumen memang nol. Kosong tidak didukung."
        self.assertIn(expected_error, blank_response.context["form"].errors["nilai_bruto"])
        self.assertIn(expected_error, blank_response.context["form"].errors["nilai_netto"])
        self.assertIn(expected_error, blank_response.context["form"].errors["pph21"])

        zero_response = self.client.post(
            reverse('dk:transaction_create'),
            self.valid_transaction_payload(
                nomor_spm="SPM-ZERO-NUMERIC",
                nilai_bruto="0",
                nilai_netto="0",
                pph21="0",
            ),
        )
        self.assertEqual(zero_response.status_code, 302)
        transaction = TransactionDetail.objects.get(nomor_spm="SPM-ZERO-NUMERIC")
        self.assertEqual(transaction.nilai_bruto, 0)
        self.assertEqual(transaction.nilai_netto, 0)
        self.assertEqual(transaction.pph21, 0)

    def test_edit_form_preserves_existing_sp2d_and_updates_fields(self):
        self.transaction.sp2d_raw = self.sp2d_sat1
        self.transaction.save(update_fields=["sp2d_raw"])

        response = self.client.post(
            reverse('dk:transaction_edit', args=[self.transaction.pk]),
            self.valid_transaction_payload(
                satker_code="SAT1",
                sp2d_raw_id=str(self.sp2d_sat2.id),
                nomor_spm="SPM001",
                no_kuitansi="KUIT001",
                deskripsi="Edited with linked SP2D",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.sp2d_raw_id, self.sp2d_sat1.id)
        self.assertEqual(self.transaction.deskripsi, "Edited with linked SP2D")

    def test_dk_renders_business_headers_and_disabled_export_controls(self):
        response = self.client.get(reverse("dk:transaction_list"))
        table = response.content.decode("utf-8").split("table-dk", 1)[1].split("</table>", 1)[0]
        business_headers = (
            "Helper", "Akun", "Bulan SP2D", "Cara Pembayaran", "Nomor SPM",
            "Tanggal SPM", "Jenis SPM", "No. Kuitansi", "No. DRPP", "Deskripsi",
            "Nilai Bruto", "Nilai Netto", "Pembebanan", "FP", "PPh21",
        )

        for header in business_headers:
            with self.subTest(header=header):
                self.assertIn(f">{header}</th>", table)
        self.assertNotIn(">SP2D Bulan</th>", table)
        # Export buttons and "Fitur ekspor belum tersedia" text removed
        content = response.content.decode("utf-8")
        self.assertNotIn("Export Excel", content)
        self.assertNotIn("Fitur ekspor belum tersedia.", content)
        # Double-click editing CSS class added
        self.assertIn("editable-row", content)

    def test_duplicate_feature_removed_and_legacy_endpoint_returns_404(self):
        with self.assertRaises(NoReverseMatch):
            reverse("dk:transaction_duplicate", args=[self.transaction.pk])

        legacy_url = f"/dk/{self.transaction.pk}/duplicate/"
        self.assertEqual(self.client.get(legacy_url).status_code, 404)
        self.assertEqual(self.client.post(legacy_url).status_code, 404)

    def test_action_buttons_follow_roles_and_remove_drpp_row_actions(self):
        admin_response = self.client.get(reverse("dk:transaction_list"))
        edit_url = reverse("dk:transaction_edit", args=[self.transaction.pk])
        archive_url = reverse("dk:transaction_archive", args=[self.transaction.pk])
        restore_url = reverse("dk:transaction_restore", args=[self.transaction.pk])
        checklist_url = reverse("documents:checklist_detail", args=[self.transaction.pk])
        self.assertContains(admin_response, 'Klik 2x untuk edit')
        self.assertContains(admin_response, archive_url)
        self.assertContains(admin_response, checklist_url)
        admin_html = admin_response.content.decode("utf-8")
        self.assertNotContains(admin_response, "Duplikat")
        self.assertNotContains(admin_response, "Lihat DRPP")
        self.assertNotContains(admin_response, ">Upload DRPP</a>", html=False)

        other = TransactionDetail.objects.create(
            satker_code="SAT2",
            akun="12345",
            nomor_spm="SPM-SAT2",
            nilai_bruto=1,
            nilai_netto=1,
        )
        self.client.login(username="op", password="password")
        operator_response = self.client.get(reverse("dk:transaction_list"))
        self.assertContains(operator_response, "SPM001")
        self.assertNotContains(operator_response, "SPM-SAT2")
        self.assertContains(operator_response, 'Klik 2x untuk edit')
        self.assertContains(operator_response, checklist_url)
        operator_html = operator_response.content.decode("utf-8")
        self.assertNotContains(operator_response, archive_url)
        self.assertNotContains(operator_response, restore_url)

        self.transaction.status_detail = TransactionDetail.StatusDetail.DIARSIPKAN
        self.transaction.save(update_fields=["status_detail"])
        operator_archived = self.client.get(
            reverse("dk:transaction_list"),
            {"archive_status": "arsip"},
        )
        self.assertContains(operator_archived, 'Klik 2x untuk edit')
        self.assertContains(operator_archived, checklist_url)
        self.assertNotContains(operator_archived, archive_url)
        self.assertNotContains(operator_archived, restore_url)
        self.transaction.status_detail = TransactionDetail.StatusDetail.DRAFT
        self.transaction.save(update_fields=["status_detail"])

        self.client.login(username="view", password="password")
        viewer_response = self.client.get(reverse("dk:transaction_list"))
        self.assertContains(viewer_response, "Lihat Checklist")
        self.assertContains(viewer_response, checklist_url)
        self.assertNotContains(viewer_response, 'Klik 2x untuk edit')
        self.assertNotContains(viewer_response, archive_url)
        self.assertNotContains(viewer_response, restore_url)
        self.assertNotContains(viewer_response, "Duplikat")
        self.assertNotContains(viewer_response, "Lihat DRPP")
        self.assertNotContains(viewer_response, ">Upload DRPP</a>", html=False)

        other.delete()

    def test_non_admin_archive_and_restore_posts_are_denied(self):
        archive_url = reverse("dk:transaction_archive", args=[self.transaction.pk])
        restore_url = reverse("dk:transaction_restore", args=[self.transaction.pk])

        for username in ("op", "view"):
            with self.subTest(username=username, action="archive"):
                self.transaction.status_detail = TransactionDetail.StatusDetail.DRAFT
                self.transaction.save(update_fields=["status_detail"])
                self.client.login(username=username, password="password")
                self.assertEqual(self.client.post(archive_url).status_code, 403)
                self.transaction.refresh_from_db()
                self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DRAFT)

            with self.subTest(username=username, action="restore"):
                self.transaction.status_detail = TransactionDetail.StatusDetail.DIARSIPKAN
                self.transaction.save(update_fields=["status_detail"])
                self.assertEqual(self.client.post(restore_url).status_code, 403)
                self.transaction.refresh_from_db()
                self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DIARSIPKAN)

    def test_admin_archive_filters_restore_and_change_logs(self):
        archive_url = reverse("dk:transaction_archive", args=[self.transaction.pk])
        restore_url = reverse("dk:transaction_restore", args=[self.transaction.pk])

        self.assertEqual(self.client.post(archive_url).status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DIARSIPKAN)
        self.assertTrue(
            self.transaction.change_logs.filter(
                field_name="status_detail",
                old_value=TransactionDetail.StatusDetail.DRAFT,
                new_value=TransactionDetail.StatusDetail.DIARSIPKAN,
            ).exists()
        )
        self.assertNotContains(self.client.get(reverse("dk:transaction_list")), "SPM001")
        archived_response = self.client.get(
            reverse("dk:transaction_list"),
            {"archive_status": "arsip"},
        )
        self.assertContains(archived_response, "SPM001")
        self.assertContains(archived_response, restore_url)

        self.assertEqual(self.client.post(restore_url).status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DRAFT)
        self.assertTrue(
            self.transaction.change_logs.filter(
                field_name="status_detail",
                old_value=TransactionDetail.StatusDetail.DIARSIPKAN,
                new_value=TransactionDetail.StatusDetail.DRAFT,
            ).exists()
        )
        self.assertContains(self.client.get(reverse("dk:transaction_list")), "SPM001")

    def test_create_transaction_admin(self):
        url = reverse('dk:transaction_create')
        data = {
            'satker_code': 'SAT2', # Admin can use a valid data-backed satker
            'akun': '12345',
            'bulan_sp2d': '2',
            'cara_pembayaran': 'UP/TUP',
            'nomor_spm': 'SPM002',
            'tanggal_spm': '2026-02-01',
            'jenis_spm': 'Gaji',
            'no_kuitansi': 'KUIT002',
            'no_drpp': 'DRPP002',
            'deskripsi': 'Test',
            'nilai_bruto': 2000,
            'nilai_netto': 1800,
            'pph21': 200,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TransactionDetail.objects.count(), 2)
        
        new_tx = TransactionDetail.objects.get(nomor_spm="SPM002")
        self.assertEqual(new_tx.satker_code, 'SAT2')
        logs = TransactionChangeLog.objects.filter(transaction=new_tx)
        self.assertTrue(logs.exists())

    def test_operator_satker_create_edit(self):
        self.client.login(username="op", password="password")
        url = reverse('dk:transaction_create')
        data = {
            'satker_code': 'SAT2', # Should be ignored and forced to SAT1
            'akun': '12345',
            'bulan_sp2d': '2',
            'cara_pembayaran': 'LS',
            'nomor_spm': 'SPM_OP',
            'tanggal_spm': '2026-02-01',
            'jenis_spm': 'Gaji',
            'no_kuitansi': 'KUIT002',
            'no_drpp': 'DRPP002',
            'deskripsi': 'Test',
            'nilai_bruto': 2000,
            'nilai_netto': 1800,
            'pph21': 200,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        new_tx = TransactionDetail.objects.get(nomor_spm="SPM_OP")
        self.assertEqual(new_tx.satker_code, 'SAT1') # Forced to SAT1

        # Edit
        edit_url = reverse('dk:transaction_edit', args=[new_tx.pk])
        data['satker_code'] = 'SAT2'
        data['deskripsi'] = 'Edited'
        response = self.client.post(edit_url, data)
        self.assertEqual(response.status_code, 302)
        new_tx.refresh_from_db()
        self.assertEqual(new_tx.deskripsi, 'Edited')
        self.assertEqual(new_tx.satker_code, 'SAT1') # Still SAT1

    def test_viewer_rejected_backend_and_ui(self):
        self.client.login(username="view", password="password")
        
        # Test UI
        list_url = reverse('dk:transaction_list')
        response = self.client.get(list_url)
        self.assertNotContains(response, "Tambah Baris Manual")
        self.assertNotContains(response, "Bulk Edit Terpilih")
        self.assertNotContains(response, 'name="ids"') # checkboxes hidden
        
        # Test Backend
        create_url = reverse('dk:transaction_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 403)

        edit_url = reverse('dk:transaction_edit', args=[self.transaction.pk])
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 403)
        
        bulk_url = reverse('dk:transaction_bulk_edit')
        response = self.client.get(bulk_url)
        self.assertEqual(response.status_code, 403)

    def test_bulk_edit_3_ids_and_preview(self):
        tx2 = TransactionDetail.objects.create(satker_code="SAT1", akun="12345", bulan_sp2d="1", nilai_bruto=1, nilai_netto=1, pph21=0)
        tx3 = TransactionDetail.objects.create(satker_code="SAT1", akun="12345", bulan_sp2d="1", nilai_bruto=1, nilai_netto=1, pph21=0)
        
        url = reverse('dk:transaction_bulk_edit')
        
        # Test GET parsing
        response = self.client.get(url, {'ids': [self.transaction.pk, tx2.pk, tx3.pk]})
        self.assertEqual(response.status_code, 200)
        
        # Test Preview
        data = {
            'action': 'preview',
            'selected_ids': [self.transaction.pk, tx2.pk, tx3.pk],
            'bulan_sp2d': '5',
            'cara_pembayaran': 'UP/TUP',
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anda akan mengubah <strong>3</strong> baris transaksi")
        self.assertContains(response, "bulan_sp2d")
        
        # Test Commit
        data['action'] = 'commit'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        self.transaction.refresh_from_db()
        tx2.refresh_from_db()
        tx3.refresh_from_db()
        self.assertEqual(self.transaction.bulan_sp2d, 5)
        self.assertEqual(tx2.bulan_sp2d, 5)
        self.assertEqual(tx3.bulan_sp2d, 5)

    def test_bulk_edit_invalid_id(self):
        url = reverse('dk:transaction_bulk_edit')
        response = self.client.get(url, {'ids': ['abc']})
        self.assertEqual(response.status_code, 302) # Redirects on error
        
        response = self.client.get(url, {'ids': [9999]})
        self.assertEqual(response.status_code, 302) # Not found

    def test_bulk_edit_archived_row(self):
        self.transaction.status_detail = TransactionDetail.StatusDetail.DIARSIPKAN
        self.transaction.save()
        
        url = reverse('dk:transaction_bulk_edit')
        response = self.client.get(url, {'ids': [self.transaction.pk]})
        self.assertEqual(response.status_code, 302) # Redirects on error

    def test_archive_repeated_and_restore_invalid_fallback(self):
        # Archive
        url = reverse('dk:transaction_archive', args=[self.transaction.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DIARSIPKAN)
        
        # Archive Repeated -> rejected
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        # Should flash warning/error
        
        # Tamper with change log to make original status invalid
        log = self.transaction.change_logs.filter(new_value="DIARSIPKAN").first()
        log.old_value = "INVALID_STATUS"
        log.save()
        
        # Restore
        restore_url = reverse('dk:transaction_restore', args=[self.transaction.pk])
        response = self.client.post(restore_url)
        self.assertEqual(response.status_code, 302)
        
        self.transaction.refresh_from_db()
        # Fallback to DRAFT
        self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DRAFT)

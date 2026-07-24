from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.dk.models import TransactionDetail, MasterAkun, TransactionChangeLog
from apps.accounts.models import Profile

User = get_user_model()

class DKTests(TestCase):
    def setUp(self):
        # Admin User
        self.user = User.objects.create_user(username="testuser", password="password", is_superuser=True)
        Profile.objects.filter(user=self.user).update(role=Profile.Role.ADMIN_PUSAT)
        
        # Operator Satker
        self.operator = User.objects.create_user(username="op", password="password")
        Profile.objects.filter(user=self.operator).update(role=Profile.Role.SATKER, satker_code="SAT1")
        
        # Viewer
        self.viewer = User.objects.create_user(username="view", password="password")
        Profile.objects.filter(user=self.viewer).update(role=Profile.Role.VIEWER)

        self.client = Client()
        self.client.login(username="testuser", password="password")
        
        self.akun = MasterAkun.objects.create(kode="12345", nama_akun="Test Akun", is_active=True)
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

    def test_helper_property(self):
        self.assertEqual(self.transaction.helper, "12345KUIT001")

    def test_create_transaction_admin(self):
        url = reverse('dk:transaction_create')
        data = {
            'satker_code': 'SAT2', # Admin can use any satker
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

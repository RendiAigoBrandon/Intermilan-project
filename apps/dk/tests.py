from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.dk.models import TransactionDetail, MasterAkun, TransactionChangeLog

User = get_user_model()

class DKTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password", is_superuser=True)
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

    def test_create_transaction(self):
        url = reverse('dk:transaction_create')
        data = {
            'satker_code': 'SAT1',
            'akun': '12345',
            'bulan_sp2d': '2',
            'cara_pembayaran': 'UP',
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
        
        # Check audit log
        new_tx = TransactionDetail.objects.get(nomor_spm="SPM002")
        logs = TransactionChangeLog.objects.filter(transaction=new_tx)
        self.assertTrue(logs.exists())

    def test_edit_transaction(self):
        url = reverse('dk:transaction_edit', args=[self.transaction.pk])
        data = {
            'satker_code': 'SAT1',
            'akun': '12345',
            'bulan_sp2d': '3',
            'cara_pembayaran': 'LS',
            'nomor_spm': 'SPM001-Edit',
            'nilai_bruto': 1500,
            'nilai_netto': 1300,
            'pph21': 200,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.bulan_sp2d, 3)
        self.assertEqual(self.transaction.nomor_spm, 'SPM001-Edit')

    def test_duplicate_transaction(self):
        url = reverse('dk:transaction_duplicate', args=[self.transaction.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TransactionDetail.objects.count(), 2)
        
        # Verify duplicate log
        dup_tx = TransactionDetail.objects.exclude(pk=self.transaction.pk).first()
        log = TransactionChangeLog.objects.filter(transaction=dup_tx, field_name="duplicated_from").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_value, str(self.transaction.pk))

    def test_archive_and_restore_transaction(self):
        # Archive
        url = reverse('dk:transaction_archive', args=[self.transaction.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DIARSIPKAN)
        
        # Check list filters
        list_url = reverse('dk:transaction_list')
        response = self.client.get(list_url)
        self.assertNotIn(self.transaction, response.context['rows'])
        
        response = self.client.get(list_url, {'archive_status': 'arsip'})
        self.assertIn(self.transaction, response.context['rows'])
        
        # Restore
        restore_url = reverse('dk:transaction_restore', args=[self.transaction.pk])
        response = self.client.post(restore_url)
        self.assertEqual(response.status_code, 302)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status_detail, TransactionDetail.StatusDetail.DRAFT)

    def test_bulk_edit(self):
        tx2 = TransactionDetail.objects.create(
            satker_code="SAT1", akun="12345", bulan_sp2d="1", cara_pembayaran="LS",
            nomor_spm="SPM002", nilai_bruto=100, nilai_netto=100, pph21=0
        )
        url = reverse('dk:transaction_bulk_edit')
        data = {
            'selected_ids': [self.transaction.pk, tx2.pk],
            'bulan_sp2d': '5',
            'cara_pembayaran': 'UP',
            'jenis_spm': 'Test Bulk',
            'status_detail': 'LENGKAP',
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        self.transaction.refresh_from_db()
        tx2.refresh_from_db()
        
        self.assertEqual(self.transaction.bulan_sp2d, 5)
        self.assertEqual(tx2.bulan_sp2d, 5)
        self.assertEqual(self.transaction.status_detail, 'LENGKAP')
        
        # Audit check
        logs = TransactionChangeLog.objects.filter(transaction=self.transaction, field_name="bulan_sp2d")
        self.assertTrue(logs.exists())

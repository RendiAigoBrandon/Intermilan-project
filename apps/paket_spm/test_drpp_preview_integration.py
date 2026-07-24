from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
from django.contrib.auth import get_user_model
from apps.dk.models import TransactionDetail
from apps.paket_spm.models import PaketSPMUpload
from apps.core.drpp_batch_parser import PARSER_VERSION as DRPP_BATCH_VERSION
import datetime

User = get_user_model()

class DRPPPreviewIntegrationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.client = Client()
        self.client.login(username="testuser", password="password")
        
        self.satker = "000000"
        self.tahun = 2026
        
        self.tx = TransactionDetail.objects.create(
            satker_code=self.satker,
            akun="521213",
            nomor_spm="00186A",
            tanggal_spm=datetime.date(2026, 6, 30),
            jenis_spm="GUP reguler",
            cara_pembayaran="UP/TUP",
            bulan_sp2d=7,
            no_kuitansi="00269/KW/019937/2026",
            nilai_bruto=Decimal("8880000"),
            nilai_netto=Decimal("8880000"),
            pembebanan="Uji Coba",
            status_detail=TransactionDetail.StatusDetail.LENGKAP
        )
        
    def test_preview_drpp_without_spm_shows_5_columns(self):
        parsed_data = {
            "parser_version": DRPP_BATCH_VERSION,
            "drpp": {
                "metadata": {
                    "nomor_drpp": "00054/DRPP/019937/2026",
                    "satker_code": self.satker,
                    "tahun": self.tahun,
                }
            },
            "drpps": [
                {
                    "metadata": {
                        "nomor_drpp": "00054/DRPP/019937/2026",
                        "satker_code": self.satker,
                        "tahun": self.tahun,
                    },
                    "items": [
                        {
                            "no_bukti": "00269/KW/019937/2026",
                            "akun": "521213",
                            "jumlah": 8880000,
                            "nilai_bruto": 8880000,
                            "no_drpp": "00054/DRPP/019937/2026"
                        }
                    ]
                }
            ],
            "kw_items": [
                {
                    "no_bukti": "00269/KW/019937/2026",
                    "akun": "521213",
                    "jumlah": 8880000,
                    "nilai_bruto": 8880000,
                    "no_drpp": "00054/DRPP/019937/2026"
                }
            ]
        }
        
        paket = PaketSPMUpload.objects.create(
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            parsed_data=parsed_data,
            original_filename="paket_spm_multi_3_files.zip",
            satker_code=self.satker,
            bulan=None
        )
        
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()
        
        response = self.client.get(reverse("paket_spm:preview"))
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "paket_spm/preview.html")
        
        paket.refresh_from_db()
        self.assertIsNone(paket.parsed_data.get("spm"), "parsed['spm'] harus tetap None untuk DRPP-only upload")
        
        content = response.content.decode("utf-8")
        self.assertIn('name="drpp-0-nomor_spm" value="00186A"', content)
        
        self.assertEqual(paket.parsed_data.get("spm_count", 0), 0)

    def test_preview_drpp_multiple_spm(self):
        # Create second TransactionDetail
        self.tx2 = TransactionDetail.objects.create(
            satker_code=self.satker,
            akun="521219",
            nomor_spm="00299B",
            tanggal_spm=datetime.date(2026, 7, 15),
            jenis_spm="LS",
            cara_pembayaran="LS",
            bulan_sp2d=8,
            no_kuitansi="00300/KW/019937/2026",
            nilai_bruto=Decimal("500000"),
            nilai_netto=Decimal("500000"),
            pembebanan="Uji Coba 2",
            status_detail=TransactionDetail.StatusDetail.LENGKAP
        )
        
        parsed_data = {
            "parser_version": DRPP_BATCH_VERSION,
            "files": [{"type": "DRPP"}, {"type": "DRPP"}],
            "drpps": [
                {
                    "status": "parsed_text",
                    "metadata": {
                        "nomor_drpp": "00054/DRPP/019937/2026",
                        "satker_code": self.satker,
                        "tahun": self.tahun,
                    },
                    "items": [
                        {
                            "no_bukti": "00269/KW/019937/2026",
                            "akun": "521213",
                            "jumlah": 8880000,
                            "nilai_bruto": 8880000,
                            "no_drpp": "00054/DRPP/019937/2026"
                        }
                    ]
                },
                {
                    "status": "parsed_text",
                    "metadata": {
                        "nomor_drpp": "00055/DRPP/019937/2026",
                        "satker_code": self.satker,
                        "tahun": self.tahun,
                    },
                    "items": [
                        {
                            "no_bukti": "00300/KW/019937/2026",
                            "akun": "521219",
                            "jumlah": 500000,
                            "nilai_bruto": 500000,
                            "no_drpp": "00055/DRPP/019937/2026"
                        }
                    ]
                }
            ],
            "kw_items": [
                {
                    "no_bukti": "00269/KW/019937/2026",
                    "akun": "521213",
                    "jumlah": 8880000,
                    "nilai_bruto": 8880000,
                    "no_drpp": "00054/DRPP/019937/2026"
                },
                {
                    "no_bukti": "00300/KW/019937/2026",
                    "akun": "521219",
                    "jumlah": 500000,
                    "nilai_bruto": 500000,
                    "no_drpp": "00055/DRPP/019937/2026"
                }
            ],
            "spm": None,
            "preview_rows": [
                {
                    "no_bukti": "00269/KW/019937/2026",
                    "akun": "521213",
                    "jumlah": 8880000,
                    "nilai_bruto": 8880000,
                    "no_drpp": "00054/DRPP/019937/2026"
                },
                {
                    "no_bukti": "00300/KW/019937/2026",
                    "akun": "521219",
                    "jumlah": 500000,
                    "nilai_bruto": 500000,
                    "no_drpp": "00055/DRPP/019937/2026"
                }
            ]
        }
        
        paket = PaketSPMUpload.objects.create(
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            parsed_data=parsed_data,
            original_filename="paket_spm_multi_drpp_multi_spm.zip",
            satker_code=self.satker,
            bulan=None
        )
        
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()
        
        response = self.client.get(reverse("paket_spm:preview"))
        
        self.assertEqual(response.status_code, 200)
        
        content = response.content.decode("utf-8")
        
        self.assertIn('name="drpp-0-nomor_spm" value="00186A"', content)
        self.assertIn('name="drpp-1-nomor_spm" value="00299B"', content)


    def test_preview_drpp_partial_match(self):
        # TransactionDetail only exists for the second item
        self.tx2 = TransactionDetail.objects.create(
            satker_code=self.satker,
            akun="521219",
            nomor_spm="00299B",
            tanggal_spm=datetime.date(2026, 7, 15),
            jenis_spm="LS",
            cara_pembayaran="LS",
            bulan_sp2d=8,
            no_kuitansi="00300/KW/019937/2026",
            nilai_bruto=Decimal("500000"),
            nilai_netto=Decimal("500000"),
            pembebanan="Uji Coba 2",
            status_detail=TransactionDetail.StatusDetail.LENGKAP
        )
        
        parsed_data = {
            "parser_version": DRPP_BATCH_VERSION,
            "files": [{"type": "DRPP"}],
            "drpps": [
                {
                    "status": "parsed_text",
                    "metadata": {
                        "nomor_drpp": "00054/DRPP/019937/2026",
                        "satker_code": self.satker,
                        "tahun": self.tahun,
                    },
                    "items": [
                        {
                            "no_bukti": "UNMATCHED/KW/019937/2026",
                            "akun": "521213",
                            "jumlah": 8880000,
                            "nilai_bruto": 8880000,
                            "no_drpp": "00054/DRPP/019937/2026"
                        },
                        {
                            "no_bukti": "00300/KW/019937/2026",
                            "akun": "521219",
                            "jumlah": 500000,
                            "nilai_bruto": 500000,
                            "no_drpp": "00054/DRPP/019937/2026"
                        }
                    ]
                }
            ],
            "kw_items": [
                {
                    "no_bukti": "UNMATCHED/KW/019937/2026",
                    "akun": "521213",
                    "jumlah": 8880000,
                    "nilai_bruto": 8880000,
                    "no_drpp": "00054/DRPP/019937/2026"
                },
                {
                    "no_bukti": "00300/KW/019937/2026",
                    "akun": "521219",
                    "jumlah": 500000,
                    "nilai_bruto": 500000,
                    "no_drpp": "00054/DRPP/019937/2026"
                }
            ]
        }
        
        paket = PaketSPMUpload.objects.create(
            uploaded_by=self.user,
            status=PaketSPMUpload.Status.PREVIEW,
            parsed_data=parsed_data,
            original_filename="paket_spm_partial_match.zip",
            satker_code=self.satker,
            bulan=None
        )
        
        session = self.client.session
        session["paket_spm_preview_id"] = paket.id
        session.save()
        
        response = self.client.get(reverse("paket_spm:preview"))
        self.assertEqual(response.status_code, 200)
        
        content = response.content.decode("utf-8")
        
        self.assertIn('name="rows-1-nomor_spm" value="00299B"', content)
        self.assertIn('name="kw-0-no_bukti" value="UNMATCHED/KW/019937/2026"', content)
        self.assertIn('name="kw-1-no_bukti" value="00300/KW/019937/2026"', content)
        
        self.assertNotIn('name="rows-0-nomor_spm" value="00299B"', content)


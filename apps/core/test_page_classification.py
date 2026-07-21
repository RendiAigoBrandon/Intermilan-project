from django.test import SimpleTestCase

from apps.core.parsers import classify_page_types


class PageClassificationTests(SimpleTestCase):
    def test_spm_header_with_generic_bukti_word_is_not_drpp(self):
        types = classify_page_types(
            "SURAT PERINTAH MEMBAYAR NOMOR SPM 00777A JUMLAH PENGELUARAN BUKTI PENGELUARAN"
        )
        self.assertIn("SPM", types)
        self.assertNotIn("DRPP", types)

    def test_drpp_table_requires_structural_evidence(self):
        types = classify_page_types(
            "DAFTAR RINCIAN PERMINTAAN PEMBAYARAN Nomor 00055/DRPP/019937/2026 "
            "BUKTI PENGELUARAN No Bukti Nama Penerima NPWP Akun Jumlah Kotor "
            "00268/KW/019937/2026"
        )
        self.assertIn("DRPP", types)
        self.assertIn("KW", types)

    def test_detail_table_can_be_detected_from_column_anchors(self):
        types = classify_page_types(
            "No SPP/SPM No SP2D Kode COA Pengeluaran Nilai Akun Potongan"
        )
        self.assertIn("DETAIL_SPP_SPM_SP2D", types)

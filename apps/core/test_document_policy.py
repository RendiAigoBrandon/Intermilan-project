from django.test import SimpleTestCase

from .document_policy import (
    AkunFamily,
    DocumentRequirement,
    SPMFamily,
    allows_empty_drpp,
    document_requirement_policy,
    get_required_documents,
    get_required_documents_for_akun_family,
    is_drpp_required,
    normalize_akun_family,
    normalize_spm_family,
)


class DocumentPolicyTests(SimpleTestCase):
    def test_normalizes_supported_families_without_batch_suffix(self):
        cases = {
            "GUP 1": SPMFamily.GUP_REGULAR,
            "GUP 17": SPMFamily.GUP_REGULAR,
            "GUP": SPMFamily.GUP_REGULAR,
            "GUP 2 (PNBP) ": SPMFamily.GUP_PNBP,
            "GUP KKP 1": SPMFamily.GUP_KKP,
            "GU KKP 9": SPMFamily.GUP_KKP,
            "UP": SPMFamily.UP,
            "TUP": SPMFamily.TUP,
            "GTUP NIHIL": SPMFamily.GTUP_NIHIL,
            "GAJI INDUK": SPMFamily.GAJI,
            "GAJI PPPK INDUK": SPMFamily.GAJI,
            "PENGHASILAN PPNPN INDUK": SPMFamily.PENGHASILAN_PPNPN,
            "SPM THR PPPK": SPMFamily.GAJI,
            "SPM Gaji 13 PPPK": SPMFamily.GAJI_13,
            "NON GAJI": SPMFamily.NON_GAJI,
            "NON GAJI KONTRAKTUAL": SPMFamily.NON_GAJI_KONTRAKTUAL,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(normalize_spm_family(label), expected)

    def test_policy_matrix(self):
        expected = {
            SPMFamily.GUP_REGULAR: DocumentRequirement.DRPP_REQUIRED,
            SPMFamily.GUP_PNBP: DocumentRequirement.DRPP_REQUIRED,
            SPMFamily.GUP_KKP: DocumentRequirement.KKP_PAYMENT_LIST_REQUIRED,
            SPMFamily.UP: DocumentRequirement.HEADER_ONLY,
            SPMFamily.TUP: DocumentRequirement.HEADER_ONLY,
            SPMFamily.GTUP_NIHIL: DocumentRequirement.CONTEXT_DEPENDENT,
            SPMFamily.GAJI: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.PENGHASILAN_PPNPN: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.TUNJANGAN_KINERJA: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.THR: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.GAJI_13: DocumentRequirement.NOMINATIVE_REQUIRED,
            SPMFamily.NON_GAJI: DocumentRequirement.CONTEXT_DEPENDENT,
            SPMFamily.NON_GAJI_KONTRAKTUAL: DocumentRequirement.SOURCE_DOCUMENT_REQUIRED,
            SPMFamily.UNKNOWN: DocumentRequirement.UNSUPPORTED_REVIEW,
        }
        for family, policy in expected.items():
            with self.subTest(family=family):
                self.assertEqual(document_requirement_policy(family), policy)

    def test_unknown_is_not_treated_as_free_of_drpp(self):
        self.assertFalse(allows_empty_drpp("tidak dikenal"))
        self.assertFalse(is_drpp_required("tidak dikenal"))
        self.assertTrue(is_drpp_required("GUP 17"))
        self.assertTrue(allows_empty_drpp("GUP KKP 8"))


class AkunFamilyDetectionTests(SimpleTestCase):
    """Test account-family detection from akun code and jenis_spm.

    Routing rules (from KK_1300.xlsx account sheet names):
    - Akun 521111 / 521119 → BELANJA_OPERASIONAL_GUP_BARANG
    - Akun 521115 → BELANJA_PEGAWAI_HONOR_PETUGAS  (sheet: Belanja Honor Operasiona)
    - Akun 521211 (Konsumsi) → BELANJA_OPERASIONAL_GUP_KONSUMSI
    - Akun 521213 (Honor) → BELANJA_PEGAWAI_HONOR_PETUGAS/PENGAJAR/POKJA
    - Akun 521219 (Non Operasional) → BELANJA_OPERASIONAL_GUP_NON_HONOR
    - Akun 522131 / 522151 → BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN
    - Akun 52214 → BELANJA_OPERASIONAL_GUP_SEWA
    - Akun 52219 → BELANJA_NON_GAJI or GUP_JASA_LAINNYA
    - Akun 825513 → BELANJA_NON_GAJI_KONTRAKTUAL
    """

    def test_akun_51xxxx_gaaji_induk(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "GAJI INDUK"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_51xxxx_gaji_pppk_induk(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "GAJI PPPK INDUK"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_51xxxx_kekurangan_gaji(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "KEKURANGAN GAJI"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_51xxxx_gaji_susulan(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "GAJI SUSULAN"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_511129_gaji_lainnya(self):
        self.assertEqual(
            normalize_akun_family("511129.0", "GAJI LAINNYA"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_511628_gaji_lainnya_pppk(self):
        self.assertEqual(
            normalize_akun_family("511628.0", "GAJI LAINNYA PPPK"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_511124_kekurangan_gaji(self):
        self.assertEqual(
            normalize_akun_family("511124.0", "KEKURANGAN GAJI"),
            AkunFamily.BELANJA_PEGAWAI_GAJI,
        )

    def test_akun_51xxxx_tunjangan_kinerja(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "TUNJANGAN KINERJA SUSULAN"),
            AkunFamily.BELANJA_PEGAWAI_TUNJANGAN,
        )

    def test_akun_512414_tunjangan_kinerja_susulan(self):
        self.assertEqual(
            normalize_akun_family("512414.0", "TUNJANGAN KINERJA SUSULAN"),
            AkunFamily.BELANJA_PEGAWAI_TUNJANGAN,
        )

    def test_akun_51xxxx_kekurangan_tunjangan(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "KEKURANGAN TUNJANGAN KINERJA"),
            AkunFamily.BELANJA_PEGAWAI_TUNJANGAN,
        )

    def test_akun_521111_gup_barang(self):
        """Akun 521111 = 'Belanja Keperluan Perkan' sheet → BARANG."""
        self.assertEqual(
            normalize_akun_family("521111.0", "GUP 1"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_BARANG,
        )

    def test_akun_521119_gup_barang(self):
        """Akun 521119 = 'Belanja Barang Operasion' sheet → BARANG."""
        self.assertEqual(
            normalize_akun_family("521119.0", "GUP 17"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_BARANG,
        )

    def test_akun_521115_honor_operasional(self):
        """Akun 521115 = 'Belanja Honor Operasiona' sheet → HONOR PETUGAS."""
        self.assertEqual(
            normalize_akun_family("521115.0", "GUP 11"),
            AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS,
        )

    def test_akun_521213_honor_petugas(self):
        """Akun 521213 = 'Honor Petugas' sheet → HONOR PETUGAS."""
        self.assertEqual(
            normalize_akun_family("521213.0", "GUP 13"),
            AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS,
        )

    def test_akun_521213_honor_pengajar(self):
        self.assertEqual(
            normalize_akun_family("521213.0", "GUP 13"),
            AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS,
        )

    def test_akun_521213_honor_pokja(self):
        self.assertEqual(
            normalize_akun_family("521213.0", "GUP 13"),
            AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS,
        )

    def test_akun_521211_konsumsi_rapat(self):
        """Akun 521211 = 'Belanja Konsumsi/Rapat' sheet → KONSUMSI."""
        self.assertEqual(
            normalize_akun_family("521211.0", "GUP 1"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_KONSUMSI,
        )

    def test_akun_521211_non_gaji(self):
        """Akun 521211 with NON GAJI → BELANJA_NON_GAJI."""
        self.assertEqual(
            normalize_akun_family("521211.0", "NON GAJI"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_521811_persediaan(self):
        """Akun 521811 = 'Belanja Persediaan' sheet → PERSEDIAAN."""
        self.assertEqual(
            normalize_akun_family("521811.0", "GUP 17"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_PERSEDIAAN,
        )

    def test_akun_523121_pemeliharaan_peralatan(self):
        """Akun 523121 = 'Belanja Pemeliharaan Per' sheet → PERALATAN."""
        self.assertEqual(
            normalize_akun_family("523121.0", "GUP 15"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_PERALATAN,
        )

    def test_akun_524111_perjadin_biasa(self):
        """Akun 524111 = 'Perjadin Biasa' sheet → PERJALANAN."""
        self.assertEqual(
            normalize_akun_family("524111.0", "GUP 1"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN,
        )

    def test_akun_524113_perjadin_dalam_kota(self):
        """Akun 524113 = 'Perjadin Dalam Kota' sheet → PERJALANAN."""
        self.assertEqual(
            normalize_akun_family("524113.0", "GUP 14"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN,
        )

    def test_akun_522111_non_gaji_langganan_listrik(self):
        """Akun 522111 = 'Langganan Listrik' → BELANJA_NON_GAJI."""
        self.assertEqual(
            normalize_akun_family("522111.0", "NON GAJI"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_522112_non_gaji_langganan_telepon(self):
        self.assertEqual(
            normalize_akun_family("522112.0", "NON GAJI"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_522113_non_gaji_langganan_air(self):
        self.assertEqual(
            normalize_akun_family("522113.0", "NON GAJI"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_522131_jasa_konsultan(self):
        """Akun 522131 = 'Belanja Jasa Konsultan' sheet → JASA KONSULTAN."""
        self.assertEqual(
            normalize_akun_family("522131.0", "GUP 3"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN,
        )

    def test_akun_522141_sewa(self):
        """Akun 522141 = 'Belanja Sewa' sheet → SEWA."""
        self.assertEqual(
            normalize_akun_family("522141.0", "GUP 3"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_SEWA,
        )

    def test_akun_522191_jasa_lainnya(self):
        """Akun 522191 = 'Belanja Jasa Lainnya' sheet → JASA LAINNYA."""
        self.assertEqual(
            normalize_akun_family("522191.0", "GUP 11"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_LAINNYA,
        )

    def test_akun_522191_penghasilan_ppnpn(self):
        """Akun 522191 with PENGHASILAN PPNPN → BELANJA_NON_GAJI."""
        self.assertEqual(
            normalize_akun_family("522191.0", "PENGHASILAN PPNPN INDUK"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_522191_thr_ppnpn(self):
        """Akun 522191 with THR PPNPN → BELANJA_NON_GAJI."""
        self.assertEqual(
            normalize_akun_family("522191.0", "SPM THR PPNPN"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_825111_up(self):
        self.assertEqual(
            normalize_akun_family("825111.0", "UP"),
            AkunFamily.BELANJA_UP,
        )

    def test_akun_825111_up_with_prefix(self):
        self.assertEqual(
            normalize_akun_family("825111.0", "311 - UP"),
            AkunFamily.BELANJA_UP,
        )

    def test_akun_825111_gtup_nihil(self):
        self.assertEqual(
            normalize_akun_family("825111.0", "GTUP NIHIL"),
            AkunFamily.BELANJA_PERJALANAN_DINAS,
        )

    def test_akun_825511_gtup_nihil(self):
        self.assertEqual(
            normalize_akun_family("825511.0", "GTUP NIHIL"),
            AkunFamily.BELANJA_PERJALANAN_DINAS,
        )

    def test_akun_532111_modal_peralatan(self):
        """Akun 532111 = 'Belanja Modal Peralatan' sheet → MMODAL."""
        self.assertEqual(
            normalize_akun_family("532111.0", "GUP 1"),
            AkunFamily.BELANJA_PERALATAN_MMODAL,
        )

    def test_akun_533121_penambahan_nilai(self):
        """Akun 533121 = 'Belanja Penambahan Nilai' sheet → MMODAL."""
        self.assertEqual(
            normalize_akun_family("533121.0", "GUP 1"),
            AkunFamily.BELANJA_PERALATAN_MMODAL,
        )

    def test_akun_unknown_returns_unknown(self):
        self.assertEqual(normalize_akun_family("", ""), AkunFamily.UNKNOWN)
        self.assertEqual(normalize_akun_family(None, None), AkunFamily.UNKNOWN)

    def test_akun_51xxxx_withGup_jenis_is_operational_gup(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "GUP 1"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR,
        )

    def test_akun_51xxxx_non_gaji(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "NON GAJI"),
            AkunFamily.BELANJA_NON_GAJI,
        )

    def test_akun_51xxxx_non_gaji_kontraktual(self):
        self.assertEqual(
            normalize_akun_family("51XXXX", "NON GAJI KONTRAKTUAL"),
            AkunFamily.BELANJA_NON_GAJI_KONTRAKTUAL,
        )

    def test_akun_825513_non_gaji_kontraktual(self):
        """Akun 825513 = Non Gaji Kontraktual → BELANJA_NON_GAJI_KONTRAKTUAL."""
        self.assertEqual(
            normalize_akun_family("825513.0", "NON GAJI KONTRAKTUAL"),
            AkunFamily.BELANJA_NON_GAJI_KONTRAKTUAL,
        )

    def test_akun_522151_jasa_profesi(self):
        """Akun 522151 = 'Jasa Profesi' → GUP JASA KONSULTAN."""
        self.assertEqual(
            normalize_akun_family("522151.0", "GUP 3"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN,
        )

    def test_akun_52114_pengiriman_surat(self):
        """Akun 52114 = 'Belanja Pengiriman Surat' → NON_HONOR."""
        self.assertEqual(
            normalize_akun_family("521114.0", "GUP 1"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR,
        )

    def test_akun_52125_peralatan_dan_mesin(self):
        """Akun 521252 = 'Belanja Peralatan dan Me' → NON_HONOR."""
        self.assertEqual(
            normalize_akun_family("521252.0", "GUP 17"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR,
        )

    def test_akun_521219_non_operasional_lainnya(self):
        """Akun 521219 = 'Non Operasional Lainnya' → NON_HONOR."""
        self.assertEqual(
            normalize_akun_family("521219.0", "GUP 1"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR,
        )

    def test_akun_521219_asuransi(self):
        """Akun 521219 with Asuransi → NON_HONOR."""
        self.assertEqual(
            normalize_akun_family("521219.0", "GUP Asuransi"),
            AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR,
        )


class RequiredDocumentsTests(SimpleTestCase):
    """Test that each account family returns the correct required documents from KK_1300.xlsx."""

    def test_gaji_family_requires_nominative_no_drpp(self):
        """GAJI family requires nominative lists (Daftar Nominatif, SSP PPh 21, dll).
        Does NOT require DRPP or SPBy."""
        family = AkunFamily.BELANJA_PEGAWAI_GAJI
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("KAK", docs)
        self.assertIn("Form permintaan/ nota dinas", docs)
        self.assertIn("Daftar Nominatif (SPJ)", docs)
        self.assertIn("SSP PPh 21", docs)
        self.assertIn("Rekapitulasi SPJ", docs)
        # GAJI does NOT require DRPP or SPBy (KK_1300 columns 13/14 are False)
        self.assertNotIn("DRPP", docs)
        self.assertNotIn("SPBy", docs)

    def test_tunjangan_family_no_drpp_spby(self):
        """TUNJANGAN KINERJA family does NOT require DRPP or SPBy."""
        family = AkunFamily.BELANJA_PEGAWAI_TUNJANGAN
        docs = get_required_documents_for_akun_family(family)
        self.assertNotIn("DRPP", docs)
        self.assertNotIn("SPBy", docs)
        self.assertIn("Daftar Nominatif (SPJ)", docs)

    def test_gup_barang_family_requires_drpp(self):
        """GUP BARANG REQUIRES DRPP and SPBy (KK_1300 columns 13/14 = True)."""
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_BARANG
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("KAK", docs)
        self.assertIn("Form permintaan/ nota dinas", docs)
        self.assertIn("SPTJM Honor PPNPN", docs)
        self.assertIn("SPJ Honor PPNPN", docs)
        self.assertIn("Realisasi BOS", docs)

    def test_gup_perjalanan_family_requires_drpp(self):
        """GUP Perjalanan Dinas REQUIRES DRPP."""
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("Surat Tugas", docs)
        self.assertIn("Surat Perjalanan Dinas (SPD) dan Bukti visum", docs)
        self.assertIn("Kuitansi dan Bukti Pembayaran", docs)
        self.assertIn("Realisasi BOS", docs)

    def test_gup_konsumsi_family_requires_drpp(self):
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_KONSUMSI
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("DRPP", docs)
        self.assertIn("SPBy", docs)
        self.assertIn("Undangan", docs)
        self.assertIn("Daftar Hadir", docs)

    def test_up_family_requires_special_docs_no_drpp(self):
        """UP family requires: SP2D, SPM, Permohonan Persetujuan UP, Super UP, dll.
        Does NOT require DRPP, SPBy, or KAK."""
        family = AkunFamily.BELANJA_UP
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("Permohonan Persetujuan UP", docs)
        self.assertIn("Super UP", docs)
        self.assertIn("Sertifikat Bendahara, PPK, PPSPM", docs)
        self.assertIn("SK Pengelola Anggaran", docs)
        self.assertIn("Persetujuan Besaran UP", docs)
        self.assertIn("Hasil Rekon SAKTI-SPAN", docs)
        self.assertIn("Specimen", docs)
        # UP does NOT require DRPP
        self.assertNotIn("DRPP", docs)
        self.assertNotIn("SPBy", docs)
        # UP does NOT require KAK (header-only mechanism)
        self.assertNotIn("KAK", docs)

    def test_non_gaji_family_no_drpp(self):
        """NON GAJI family does NOT require DRPP or SPBy."""
        family = AkunFamily.BELANJA_NON_GAJI
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("KAK", docs)
        self.assertIn("Form permintaan/ nota dinas", docs)
        self.assertIn("Kuitansi dan Bukti Pembayaran", docs)
        self.assertIn("Faktur/Invoice", docs)
        self.assertIn("Realisasi BOS", docs)
        # NON GAJI does NOT require DRPP or SPBy
        self.assertNotIn("DRPP", docs)
        self.assertNotIn("SPBy", docs)

    def test_non_gaji_kontraktual_family_no_drpp(self):
        """NON GAJI KONTRAKTUAL requires Kontrak/SPK and nominative lists, NO DRPP."""
        family = AkunFamily.BELANJA_NON_GAJI_KONTRAKTUAL
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("Kontrak/SPK", docs)
        self.assertIn("Kuitansi dan Bukti Pembayaran", docs)
        self.assertIn("Daftar Nominatif PPNPN/PPPK/THR", docs)
        self.assertIn("SSP PPh 21", docs)
        self.assertIn("SPTJM", docs)
        # NON GAJI KONTRAKTUAL does NOT require DRPP or SPBy
        self.assertNotIn("DRPP", docs)
        self.assertNotIn("SPBy", docs)

    def test_perjalanan_dinas_family_requires_drpp(self):
        """GTUP NIHIL (BELANJA_PERJALANAN_DINAS) REQUIRES DRPP."""
        family = AkunFamily.BELANJA_PERJALANAN_DINAS
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("Surat Tugas", docs)
        self.assertIn("Surat Perjalanan Dinas (SPD) dan Bukti visum", docs)
        self.assertIn("Kuitansi dan Bukti Pembayaran", docs)

    def test_peralatan_mmodal_family_requires_drpp(self):
        """BELANJA_PERALATAN_MMODAL REQUIRES DRPP and extensive procurement docs."""
        family = AkunFamily.BELANJA_PERALATAN_MMODAL
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("RUP", docs)
        self.assertIn("Kontrak/Surat Perjanjian", docs)
        self.assertIn("BAST", docs)
        self.assertIn("Bukti Pembayaran", docs)

    def test_gup_jasa_konsultan_family_requires_drpp(self):
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("BAPP", docs)
        self.assertIn("BAST", docs)
        self.assertIn("BAP", docs)

    def test_gup_sewa_family_requires_drpp(self):
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_SEWA
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("SPK/Surat Perjanjian", docs)

    def test_gup_persediaan_family_requires_drpp(self):
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_PERSEDIAAN
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)

    def test_gup_honor_petugas_requires_drpp(self):
        family = AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("SK KPA tentang Honor", docs)
        self.assertIn("Daftar Rekapitulasi Belanja Honor", docs)
        self.assertIn("SSP PPh 21", docs)

    def test_gup_jasa_lainnya_requires_drpp(self):
        family = AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_LAINNYA
        docs = get_required_documents_for_akun_family(family)
        self.assertIn("SPBy", docs)
        self.assertIn("DRPP", docs)
        self.assertIn("BAPP", docs)
        self.assertIn("BAST", docs)

    def test_unknown_akun_returns_empty_docs(self):
        docs = get_required_documents_for_akun_family(AkunFamily.UNKNOWN)
        self.assertEqual(docs, [])

    def test_get_required_documents_with_float_akun(self):
        """Akun codes can come as floats from Excel (e.g., '521111.0')."""
        docs = get_required_documents("521111.0", "GUP 1")
        self.assertIn("DRPP", docs)
        self.assertIn("SPBy", docs)

    def test_get_required_documents_with_string_akun(self):
        docs = get_required_documents("51XXXX", "GAJI INDUK")
        self.assertIn("SPM", docs)
        self.assertIn("Daftar Nominatif (SPJ)", docs)
        self.assertNotIn("DRPP", docs)

    def test_akun_family_requires_drpp_via_akun_check(self):
        """Account 521111 (GUP) requires DRPP via akun detection."""
        docs = get_required_documents("521111.0", "GUP 1")
        self.assertIn("DRPP", docs)
        self.assertIn("SPBy", docs)
        self.assertIn("KAK", docs)
        self.assertNotIn("Daftar Nominatif (SPJ)", docs)  # GUP, not GAJI

    def test_akun_825111_up_requires_special_docs(self):
        """Account 825111 (UP) requires the UP-specific document set."""
        docs = get_required_documents("825111.0", "UP")
        self.assertIn("SP2D", docs)
        self.assertIn("SPM", docs)
        self.assertIn("Permohonan Persetujuan UP", docs)
        self.assertIn("Super UP", docs)
        self.assertNotIn("DRPP", docs)
        self.assertNotIn("SPBy", docs)

    def test_akun_825513_kontraktual_has_kontrak_sppm(self):
        """Account 825513 (Non Gaji Kontraktual) requires Kontrak/SPK."""
        docs = get_required_documents("825513.0", "NON GAJI KONTRAKTUAL")
        self.assertIn("Kontrak/SPK", docs)
        self.assertIn("Daftar Nominatif PPNPN/PPPK/THR", docs)
        self.assertNotIn("DRPP", docs)

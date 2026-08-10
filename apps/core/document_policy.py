"""Kebijakan kebutuhan dokumen berdasarkan keluarga Jenis SPM dan keluarga Akun.

Modul ini mendeskripsikan kebijakan dokumen berdasarkan:
1. Keluarga Jenis SPM (GAJI, GUP, UP, dll.) - dari field jenis_spm
2. Keluarga Akun (Belanja Pegawai, Belanja Operasional GUP, UP, dll.) - dari field akun

AKUN-FAMILY DETECTION:
- Keluarga Akun ditentukan PRIMER dari field `akun` (kode akun)
- Untuk akun 51XXXX, keluarga spesifik ditentukan dari `jenis_spm`
- Mapping berdasarkan struktur KK_1300.xlsx

AKUN FAMILY → REQUIRED DOCS (dari KK_1300.xlsx):
- BELANJA_PEGAWAI_GAJI        → SP2D, SPM, KAK, Form permintaan/nota dinas, SPTJM, Rekapitulasi SPJ,
                                  Rekap Per Gol, Daftar Nominatif (SPJ), Daftar Perubahan Gaji, Halaman Depan, SSP PPh 21, Realisasi BOS
- BELANJA_PEGAWAI_TUNJANGAN  → SP2D, SPM, KAK, Form permintaan/nota dinas, SPTJM, Rekapitulasi SPJ,
                                  Daftar Nominatif (SPJ), SSP PPh 21, Realisasi BOS
- BELANJA_PEGAWAI_HONOR_*    → SP2D, SPM, SPBy, DRPP, KAK, Form permintaan/nota dinas, SK KPA tentang Honor,
                                  Daftar Rekapitulasi Belanja Honor, Bukti pembayaran, SSP PPh 21, Realisasi BOS
                                  (+ tambahan: Surat Tugas, Laporan Inda/Inas, Jadwal Kegiatan, Daftar Hadir / Surat Perjanjian Kerja / BAST / Matriks Alokasi Pokja / Laporan Tim Pokja)
- BELANJA_OPERASIONAL_GUP    → SP2D, SPM, SPBy, DRPP, KAK, Form permintaan/nota dinas,
                                  + dokumen pendukung per sub-tipe (barang, honor, perjalanan dinas, konsumsi, dll.)
- BELANJA_UP                 → SP2D, SPM, Permohonan Persetujuan UP, Super UP, Sertifikat Bendahara/PPK/PPSPM,
                                  SK Pengelola Anggaran, Persetujuan Besaran UP, Hasil Rekon SAKTI-SPAN, Specimen
- BELANJA_NON_GAJI_KONTRAKTUAL → Kontrak/SPK, Kuitansi/Bukti Pembayaran, DAFTAR NOMINATIF PPNPN/PPPK/THR,
                                  SSP PPh 21, SPTJM, KAK, Form permintaan/nota dinas
                                  (Dari akun 825513)
"""

from enum import Enum
import re


class SPMFamily(str, Enum):
    GUP_REGULAR = "GUP_REGULAR"
    GUP_PNBP = "GUP_PNBP"
    GUP_KKP = "GUP_KKP"
    UP = "UP"
    TUP = "TUP"
    GTUP_NIHIL = "GTUP_NIHIL"
    GAJI = "GAJI"
    PENGHASILAN_PPNPN = "PENGHASILAN_PPNPN"
    TUNJANGAN_KINERJA = "TUNJANGAN_KINERJA"
    THR = "THR"
    GAJI_13 = "GAJI_13"
    NON_GAJI = "NON_GAJI"
    NON_GAJI_KONTRAKTUAL = "NON_GAJI_KONTRAKTUAL"
    UNKNOWN = "UNKNOWN"


class AkunFamily(str, Enum):
    BELANJA_PEGAWAI_GAJI = "BELANJA_PEGAWAI_GAJI"
    BELANJA_PEGAWAI_TUNJANGAN = "BELANJA_PEGAWAI_TUNJANGAN"
    BELANJA_PEGAWAI_HONOR_PETUGAS = "BELANJA_PEGAWAI_HONOR_PETUGAS"
    BELANJA_PEGAWAI_HONOR_PENGAJAR = "BELANJA_PEGAWAI_HONOR_PENGAJAR"
    BELANJA_PEGAWAI_HONOR_POKJA = "BELANJA_PEGAWAI_HONOR_POKJA"
    BELANJA_OPERASIONAL_GUP_BARANG = "BELANJA_OPERASIONAL_GUP_BARANG"
    BELANJA_OPERASIONAL_GUP_PERJALANAN = "BELANJA_OPERASIONAL_GUP_PERJALANAN"
    BELANJA_OPERASIONAL_GUP_KONSUMSI = "BELANJA_OPERASIONAL_GUP_KONSUMSI"
    BELANJA_OPERASIONAL_GUP_PERALATAN = "BELANJA_OPERASIONAL_GUP_PERALATAN"
    BELANJA_OPERASIONAL_GUP_SEWA = "BELANJA_OPERASIONAL_GUP_SEWA"
    BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN = "BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN"
    BELANJA_OPERASIONAL_GUP_JASA_LAINNYA = "BELANJA_OPERASIONAL_GUP_JASA_LAINNYA"
    BELANJA_OPERASIONAL_GUP_PERSEDIAAN = "BELANJA_OPERASIONAL_GUP_PERSEDIAAN"
    BELANJA_OPERASIONAL_GUP_NON_HONOR = "BELANJA_OPERASIONAL_GUP_NON_HONOR"
    BELANJA_PERJALANAN_DINAS = "BELANJA_PERJALANAN_DINAS"
    BELANJA_PERALATAN_MMODAL = "BELANJA_PERALATAN_MMODAL"
    BELANJA_UP = "BELANJA_UP"
    BELANJA_NON_GAJI_KONTRAKTUAL = "BELANJA_NON_GAJI_KONTRAKTUAL"
    BELANJA_NON_GAJI = "BELANJA_NON_GAJI"
    UNKNOWN = "UNKNOWN"


class DocumentRequirement(str, Enum):
    DRPP_REQUIRED = "DRPP_REQUIRED"
    KKP_PAYMENT_LIST_REQUIRED = "KKP_PAYMENT_LIST_REQUIRED"
    NOMINATIVE_REQUIRED = "NOMINATIVE_REQUIRED"
    SOURCE_DOCUMENT_REQUIRED = "SOURCE_DOCUMENT_REQUIRED"
    HEADER_ONLY = "HEADER_ONLY"
    CONTEXT_DEPENDENT = "CONTEXT_DEPENDENT"
    UNSUPPORTED_REVIEW = "UNSUPPORTED_REVIEW"


# Base documents shared across families
_BASE_DOCS_GAJI = [
    "SP2D", "SPM", "KAK", "Form permintaan/ nota dinas",
    "SPTJM (Khusus Tukin)", "Rekapitulasi SPJ", "Rekap Per Gol (Khusus Gaji)",
    "Daftar Nominatif (SPJ)", "Daftar Perubahan Gaji (Khusus Gaji)",
    "Halaman Depan", "SSP PPh 21", "Realisasi BOS",
]

_BASE_DOCS_TUNJANGAN = [
    "SP2D", "SPM", "KAK", "Form permintaan/ nota dinas",
    "SPTJM (Khusus Tukin)", "Rekapitulasi SPJ",
    "Daftar Nominatif (SPJ)", "SSP PPh 21", "Realisasi BOS",
]

_BASE_DOCS_GUP = [
    "SP2D", "SPM", "SPBy", "DRPP", "KAK", "Form permintaan/ nota dinas",
]

# Account-family → required document list
AKUN_FAMILY_REQUIRED_DOCS = {
    # Belanja Pegawai - Gaji & Kekurangan Gaji
    # Akun 51XXXX dengan jenis SPM: GAJI INDUK, GAJI PPPK INDUK, KEKURANGAN GAJI, GAJI SUSULAN, GAJI LAINNYA, GAJI LAINNYA PPPK
    # Account 511124, 511129, 511628, 51XXXX
    AkunFamily.BELANJA_PEGAWAI_GAJI: _BASE_DOCS_GAJI,

    # Belanja Pegawai - Tunjangan Kinerja & Kekurangan Tunjangan
    # Akun 512414, 51XXXX dengan jenis SPM: TUNJANGAN KINERJA, KEKURANGAN TUNJANGAN KINERJA
    AkunFamily.BELANJA_PEGAWAI_TUNJANGAN: _BASE_DOCS_TUNJANGAN,

    # Belanja Pegawai - Honor Operasional
    # Akun 521115
    AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS: _BASE_DOCS_GUP + [
        "SK KPA tentang Honor", "Daftar Rekapitulasi Belanja Honor",
        "Bukti pembayaran", "SSP PPh 21", "Realisasi BOS",
    ],

    # Belanja Pegawai - Honor Pengajar
    AkunFamily.BELANJA_PEGAWAI_HONOR_PENGAJAR: _BASE_DOCS_GUP + [
        "SK KPA tentang Honor", "Surat Tugas", "Laporan Inda/Inas",
        "Jadwal Kegiatan", "Daftar Hadir", "Daftar Rekapitulasi Belanja Honor",
        "Bukti pembayaran", "SSP PPh 21", "Realisasi BOS",
    ],

    # Belanja Pegawai - Honor Pokja
    AkunFamily.BELANJA_PEGAWAI_HONOR_POKJA: _BASE_DOCS_GUP + [
        "SK KPA tentang Honor", "Matriks Alokasi Pokja Tim Pelaksana",
        "Laporan Tim Pokja", "Daftar Rekapitulasi Belanja Honor",
        "Bukti pembayaran", "SSP PPh 21", "Realisasi BOS",
    ],

    # Belanja Operasional GUP - Barang (521111, 521119)
    # Akun 521111 (Belanja Keperluan Perkan), 521119 (Belanja Barang Operasional)
    # Jenis SPM: GUP N, GU KKP N
    AkunFamily.BELANJA_OPERASIONAL_GUP_BARANG: _BASE_DOCS_GUP + [
        "Faktur Pembelian", "Tanda Terima",
        "Kuitansi dan Bukti Pembayaran", "SSP",
        "SPJ Honor PPNPN", "SPTJM Honor PPNPN", "Realisasi BOS",
        "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Perjalanan Dinas (524111, 524113)
    # Akun 524111 (Perjadin Biasa), 524113 (Perjadin Dalam Kota), 524114
    AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN: _BASE_DOCS_GUP + [
        "Surat Tugas",
        "Surat Perjalanan Dinas (SPD) dan Bukti visum",
        "Presensi dan Uang Makan",
        "Rincian biaya perjalanan dinas", "Bukti transportasi",
        "Bukti penginapan (Billing Hotel)",
        "Laporan perjalanan dinas dan dokumentasi",
        "Rekapitulasi perjalanan dinas",
        "Kuitansi dan Bukti Pembayaran",
        "Surat Pernyataan tidak menggunakan kendaraan dinas",
        "Realisasi BOS",
    ],

    # Belanja Operasional GUP - Konsumsi (521211)
    # Akun 521211 (Konsumsi Rapat)
    AkunFamily.BELANJA_OPERASIONAL_GUP_KONSUMSI: _BASE_DOCS_GUP + [
        "Undangan", "Daftar Hadir", "Notulen",
        "Dokumentasi", "Kuitansi dan Bukti pembayaran",
        "SSP", "Realisasi BOS", "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Peralatan & Pemeliharaan (523121, 523111, 523119)
    # Akun 523121 (Belanja Pemeliharaan Peralatan), 523111, 523119
    AkunFamily.BELANJA_OPERASIONAL_GUP_PERALATAN: _BASE_DOCS_GUP + [
        "Faktur Pembelian", "Tanda Terima",
        "Kuitansi dan Bukti Pembayaran", "SSP",
        "Realisasi BOS", "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Sewa (522141)
    AkunFamily.BELANJA_OPERASIONAL_GUP_SEWA: _BASE_DOCS_GUP + [
        "SPK/Surat Perjanjian", "FC NPWP",
        "Berita Acara Serah Terima Hasil Pekerjaan",
        "Berita Acara Pembayaran", "Invoice",
        "Kuitansi dan Bukti Pembayaran", "SSP",
        "Realisasi BOS", "Fc Rekening Koran",
        "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Jasa Konsultan (522131)
    AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN: _BASE_DOCS_GUP + [
        "Undangan", "Daftar Hadir",
        "Kuitansi dan Bukti Pembayaran",
        "Bukti Prestasi Kerja", "Laporan Pelaksanaan Kegiatan",
        "BAPP", "BAST", "BAP", "SSP",
        "Realisasi BOS", "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Jasa Lainnya (522191)
    AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_LAINNYA: _BASE_DOCS_GUP + [
        "Undangan", "Daftar Hadir",
        "Kuitansi dan Bukti Pembayaran",
        "Bukti Prestasi Kerja", "Laporan Pelaksanaan Kegiatan",
        "BAPP", "BAST", "BAP", "SSP",
        "Realisasi BOS", "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Persediaan (521811)
    AkunFamily.BELANJA_OPERASIONAL_GUP_PERSEDIAAN: _BASE_DOCS_GUP + [
        "SPK/Surat Perjanjian/Surat Pesanan", "Tanda Terima",
        "Kuitansi dan Bukti Pembayaran", "Faktur/Invoice",
        "SSP", "Realisasi BOS", "Pencatatan Non Tender",
    ],

    # Belanja Operasional GUP - Non-Honor (other 52XXXX not listed above)
    # 521114 (Pengiriman Surat), 521219 (Non Operasional), 521252 (Peralatan Meja)
    # 521115 when used as operational (kebetulan honor juga), 522111-522119 (Langganan)
    # Maps to the generic GUP non-honor
    AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR: _BASE_DOCS_GUP + [
        "Kuitansi dan Bukti Pembayaran", "Realisasi BOS", "Pencatatan Non Tender",
    ],

    # Belanja Perjalanan Dinas (824111 equivalent - GTUP uses these)
    # Akun 825111/825511 with Jenis SPM: GTUP NIHIL → uses same docs as GUP Perjalanan
    # But these are in BELANJA_PERJALANAN_DINAS (account-based)
    AkunFamily.BELANJA_PERJALANAN_DINAS: [
        "SP2D", "SPM", "SPBy", "DRPP", "KAK", "Form permintaan/ nota dinas",
        "Surat Tugas",
        "Surat Perjalanan Dinas (SPD) dan Bukti visum",
        "Presensi dan Uang Makan",
        "Rincian biaya perjalanan dinas", "Bukti transportasi",
        "Bukti penginapan (Billing Hotel)",
        "Laporan perjalanan dinas dan dokumentasi",
        "Rekapitulasi perjalanan dinas",
        "Kuitansi dan Bukti Pembayaran",
        "Surat Pernyataan tidak menggunakan kendaraan dinas",
        "Realisasi BOS",
    ],

    # Belanja Modal/Peralatan (532111, 533121)
    AkunFamily.BELANJA_PERALATAN_MMODAL: _BASE_DOCS_GUP + [
        "Formulir Permintaan", "Penetapan Spek Teknis/KAK",
        "RUP", "Kertas Kerja Penyusunan RAB",
        "BA Penyusunan RAB dan Spesifikasi",
        "BA Penetapan Rancangan Kontrak",
        "Permintaan Pemilihan Penyedia Barang/Jasa",
        "BA Reviu Dokumen Persiapan Pengadaan Langsung",
        "Dokumen Pemilihan",
        "Surat Permintaan Penawaran", "Surat Penawaran Harga",
        "BA Pembukaan Dokumen Penawaran",
        "Ba Evaluasi Administrasi, Kualifikasi, Teknis, dan Harga",
        "BA Klarifikasi Teknis Dan Negosiasi Harga",
        "BA Hasil Pelelangan/Seleksi",
        "Surat Penunjukan Penyedia Barang/Jasa",
        "Kontrak/Surat Perjanjian",
        "BAST",
        "Bukti Pembayaran",
        "Realisasi BOS",
    ],

    # UP/TUP (825111, 825511)
    AkunFamily.BELANJA_UP: [
        "SP2D", "SPM", "Permohonan Persetujuan UP",
        "Super UP",
        "Sertifikat Bendahara, PPK, PPSPM",
        "SK Pengelola Anggaran",
        "Persetujuan Besaran UP",
        "Hasil Rekon SAKTI-SPAN",
        "Specimen",
    ],

    # Non Gaji Kontraktual (825513)
    # Dokumen: Kontrak/SPK, KW, Daftar Nominatif PPNPN/PPPK/THR, SSP PPh 21, SPTJM, KAK, Form permintaan
    # Note: Does NOT use DRPP or SPBy
    AkunFamily.BELANJA_NON_GAJI_KONTRAKTUAL: [
        "SP2D", "SPM", "KAK", "Form permintaan/ nota dinas",
        "Kontrak/SPK",
        "Kuitansi dan Bukti Pembayaran",
        "Daftar Nominatif PPNPN/PPPK/THR",
        "SSP PPh 21", "SPTJM",
    ],

    # Non Gaji (522111, 522112, 522113, 522119, 522191 for NON GAJI Jenis SPM)
    # Does NOT require DRPP or SPBy (it's not a GUP mechanism)
    AkunFamily.BELANJA_NON_GAJI: [
        "SP2D", "SPM", "KAK", "Form permintaan/ nota dinas",
        "Kuitansi dan Bukti Pembayaran",
        "Faktur/Invoice",
        "Realisasi BOS",
    ],
}


POLICY_BY_FAMILY = {
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


def _normalized_label(value):
    text = str(value or "").upper().replace("_", " ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_spm_family(value):
    """Kembalikan keluarga stabil tanpa mengubah label asli Jenis SPM."""
    text = _normalized_label(value)
    if not text:
        return SPMFamily.UNKNOWN
    text = re.sub(r"^SPM\s+", "", text)

    # Urutan spesifik harus mendahului label yang merupakan substring-nya.
    if re.search(r"\b(?:GUP|GU)\s+KKP(?:\s+\d+)?$", text):
        return SPMFamily.GUP_KKP
    if re.search(r"\bGUP\s+2\s+PNBP(?:\s+\d+)?$", text) or (
        text.startswith("GUP") and "PNBP" in text
    ):
        return SPMFamily.GUP_PNBP
    if re.fullmatch(r"GUP(?:\s+\d+)?", text):
        return SPMFamily.GUP_REGULAR
    if re.fullmatch(r"GTUP\s+NIHIL(?:\s+\d+)?", text):
        return SPMFamily.GTUP_NIHIL
    if re.fullmatch(r"TUP(?:\s+\d+)?", text):
        return SPMFamily.TUP
    if re.fullmatch(r"UP(?:\s+\d+)?", text):
        return SPMFamily.UP
    if text.startswith("NON GAJI KONTRAKTUAL"):
        return SPMFamily.NON_GAJI_KONTRAKTUAL
    if text.startswith("NON GAJI"):
        return SPMFamily.NON_GAJI
    if "PENGHASILAN PPNPN" in text or re.search(r"\bPPNPN\b", text):
        return SPMFamily.PENGHASILAN_PPNPN
    if "TUNJANGAN KINERJA" in text or re.search(r"\bTUKIN\b", text):
        return SPMFamily.TUNJANGAN_KINERJA
    if re.search(r"\bGAJI\s+(?:KE\s*)?13\b", text):
        return SPMFamily.GAJI_13
    if re.search(r"\bTHR\b", text):
        return SPMFamily.GAJI
    if re.search(r"\bGAJI\b", text):
        return SPMFamily.GAJI
    return SPMFamily.UNKNOWN


def document_requirement_policy(value):
    family = value if isinstance(value, SPMFamily) else normalize_spm_family(value)
    return POLICY_BY_FAMILY.get(family, DocumentRequirement.UNSUPPORTED_REVIEW)


def is_drpp_required(value):
    return document_requirement_policy(value) == DocumentRequirement.DRPP_REQUIRED


def allows_empty_drpp(value):
    family = value if isinstance(value, SPMFamily) else normalize_spm_family(value)
    return family != SPMFamily.UNKNOWN and not is_drpp_required(family)


# =============================================================================
# Account-Family Detection (based on field `akun` and optionally `jenis_spm`)
# =============================================================================

def normalize_akun_family(akun, jenis_spm=""):
    """
    Kembalikan keluarga Akun (AkunFamily) berdasarkan field `akun` dan `jenis_spm`.

    Akun codes arrive as float strings (e.g. "521111.0") from Excel, or as plain
    strings (e.g. "51XXXX", "825513"). Normalize by extracting the leading
    numeric digits.

    Routing key:
    - Akun "51XXXX" (literal 5 chars) or 5-digit numeric (51XXX) → Belanja Pegawai
    - Akun starting with "521111" / "521119" → BELANJA_OPERASIONAL_GUP_BARANG
    - Akun starting with "52114" → BELANJA_OPERASIONAL_GUP_NON_HONOR
    - Akun starting with "52115" → BELANJA_PEGAWAI_HONOR_PETUGAS
    - Akun starting with "521211" → BELANJA_OPERASIONAL_GUP_KONSUMSI
    - Akun starting with "521213" → BELANJA_PEGAWAI_HONOR_*
    - Akun starting with "521219" → BELANJA_OPERASIONAL_GUP_NON_HONOR
    - Akun starting with "52125" → BELANJA_OPERASIONAL_GUP_NON_HONOR
    - Akun starting with "52181" → BELANJA_OPERASIONAL_GUP_PERSEDIAAN
    - Akun starting with "52211" / "52212" → BELANJA_NON_GAJI
    - Akun starting with "522131" → BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN
    - Akun starting with "52213" → BELANJA_NON_GAJI
    - Akun starting with "52214" → BELANJA_OPERASIONAL_GUP_SEWA
    - Akun starting with "52215" → BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN
    - Akun starting with "52219" → BELANJA_NON_GAJI or GUP_JASA_LAINNYA
    - Akun starting with "523" / "524" → BELANJA_OPERASIONAL_GUP_PERALATAN or PERJALANAN
    - Akun starting with "532111" → BELANJA_PERALATAN_MMODAL
    - Akun starting with "533121" → BELANJA_PERALATAN_MMODAL
    - Akun starting with "825513" → BELANJA_NON_GAJI_KONTRAKTUAL
    - Akun starting with "825111" / "825511" → BELANJA_UP or PERJALANAN_DINAS
    """
    if not akun:
        return AkunFamily.UNKNOWN

    text = str(akun).strip()
    # Remove trailing .0 from float strings (e.g. "511124.0" → "511124")
    if text.endswith(".0"):
        text = text[:-2]
    elif "." in text:
        text = text.rstrip("0").rstrip(".")

    text_lower = (jenis_spm or "").lower()

    # === Akun 51XXXX literal or 5/6-digit numeric prefix (Belanja Pegawai) ===
    # Check literal "51XXXX" first, then 5-digit (51XXX) or 6-digit (511XXX) numeric codes
    if text == "51XXXX" or (
        text.isdigit()
        and len(text) in (5, 6)
        and text.startswith("51")
    ):
        # IMPORTANT: check NON GAJI / NON GAJI KONTRAKTUAL BEFORE "gaji" substring
        # because "gaji" would match first and override
        if "non gaji kontraktual" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI_KONTRAKTUAL
        if "non gaji" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI
        if "tunjangan kinerja" in text_lower or "kekurangan tunjangan" in text_lower:
            return AkunFamily.BELANJA_PEGAWAI_TUNJANGAN
        if "gup" in text_lower:
            return AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR
        if "gaji" in text_lower:
            return AkunFamily.BELANJA_PEGAWAI_GAJI
        # Default: Belanja Pegawai Gaji
        return AkunFamily.BELANJA_PEGAWAI_GAJI

    # === Akun 521111 / 521119 - Belanja Keperluan Perkantoran / Barang ===
    if text.startswith("521111") or text.startswith("521119"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_BARANG

    # === Akun 521114 - Pengiriman Surat ===
    if text.startswith("521114"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR

    # === Akun 521115 - Honor Operasional ===
    if text.startswith("521115"):
        return AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS

    # === Akun 521211 - Belanja Konsumsi Rapat ===
    if text.startswith("521211"):
        if "non gaji" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI
        return AkunFamily.BELANJA_OPERASIONAL_GUP_KONSUMSI

    # === Akun 521213 - Honor Pegawai (Petugas / Pengajar / Pokja) ===
    if text.startswith("521213"):
        if "pokja" in text_lower:
            return AkunFamily.BELANJA_PEGAWAI_HONOR_POKJA
        if "pengajar" in text_lower:
            return AkunFamily.BELANJA_PEGAWAI_HONOR_PENGAJAR
        return AkunFamily.BELANJA_PEGAWAI_HONOR_PETUGAS

    # === Akun 521219 - Non Operasional Lainnya / Pengiriman / Asuransi ===
    if text.startswith("521219"):
        if "asuransi" in text_lower or "pengiriman" in text_lower:
            return AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR
        if "non gaji" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI
        return AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR

    # === Akun 52125 - Belanja Peralatan dan Mesin-Medik ===
    if text.startswith("52125"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_NON_HONOR

    # === Akun 52181 - Persediaan ===
    if text.startswith("52181"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_PERSEDIAAN

    # === Akun 52211 - Langganan Listrik ===
    if text.startswith("52211"):
        return AkunFamily.BELANJA_NON_GAJI

    # === Akun 52212 - Langganan Telepon / Air ===
    if text.startswith("52212"):
        return AkunFamily.BELANJA_NON_GAJI

    # === Akun 52213 - Langganan Daya / Jasa Konsultan ===
    if text.startswith("522131"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN
    if text.startswith("52213"):
        return AkunFamily.BELANJA_NON_GAJI

    # === Akun 52214 - Sewa ===
    if text.startswith("52214"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_SEWA

    # === Akun 52215 - Jasa Profesi ===
    if text.startswith("52215"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_KONSULTAN

    # === Akun 52219 - Jasa Lainnya ===
    if text.startswith("52219"):
        if "penghasilan ppnpn" in text_lower or "ppnpn" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI
        if "non gaji" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI
        if "thr" in text_lower:
            return AkunFamily.BELANJA_NON_GAJI
        return AkunFamily.BELANJA_OPERASIONAL_GUP_JASA_LAINNYA

    # === Akun 523XXX / 524XXX - Pemeliharaan / Perjalanan Dinas ===
    if text.startswith("524111"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN
    if text.startswith("524113"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN
    if text.startswith("524114"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN
    if text.startswith("524119"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_PERJALANAN
    if text.startswith("523") or text.startswith("524"):
        return AkunFamily.BELANJA_OPERASIONAL_GUP_PERALATAN

    # === Akun 532111 - Belanja Modal Peralatan ===
    if text.startswith("532111"):
        return AkunFamily.BELANJA_PERALATAN_MMODAL

    # === Akun 533121 - Belanja Penambahan Nilai Aset Tetap ===
    if text.startswith("533121"):
        return AkunFamily.BELANJA_PERALATAN_MMODAL

    # === Akun 825513 - Non Gaji Kontraktual ===
    if text.startswith("825513"):
        return AkunFamily.BELANJA_NON_GAJI_KONTRAKTUAL

    # === Akun 8251XX / 8255XX ===
    if text.startswith("825111") or text.startswith("825511"):
        if "gtup nihil" in text_lower or "nihil" in text_lower:
            return AkunFamily.BELANJA_PERJALANAN_DINAS
        return AkunFamily.BELANJA_UP
    if text.startswith("8251") or text.startswith("8255"):
        if "gtup nihil" in text_lower or "nihil" in text_lower:
            return AkunFamily.BELANJA_PERJALANAN_DINAS
        return AkunFamily.BELANJA_UP

    return AkunFamily.UNKNOWN


def get_required_documents_for_akun_family(family):
    """
    Kembalikan daftar nama dokumen yang DIWAJIBKAN untuk keluarga akun ini.
    Berdasarkan mapping AKUN_FAMILY_REQUIRED_DOCS.
    """
    if isinstance(family, str):
        try:
            family = AkunFamily(family)
        except ValueError:
            return []
    return list(AKUN_FAMILY_REQUIRED_DOCS.get(family, []))


def get_required_documents(akun, jenis_spm):
    """
    Kembalikan daftar dokumen yang DIWAJIBKAN untuk transaksi ini.
    Menggunakan account-family detection.
    """
    family = normalize_akun_family(akun, jenis_spm)
    return get_required_documents_for_akun_family(family)

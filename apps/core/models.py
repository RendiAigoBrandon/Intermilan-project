from django.db import models
from typing import Optional


class MonitoringSummary(models.Model):
    class Source(models.TextChoices):
        EXCEL_SEED = "excel_seed", "Excel Seed"
        CALCULATED = "calculated", "Calculated"
        MANUAL = "manual", "Manual"
        MIXED = "mixed", "Mixed"

    satker_code = models.CharField(max_length=32)
    satker_label = models.CharField(max_length=100)
    bulan = models.CharField(max_length=20)
    bulan_number = models.PositiveSmallIntegerField()
    tahun = models.PositiveSmallIntegerField()
    fa16_bulan_ini = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    intermilan_bulan_ini = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    intermilan_sd_bulan_ini = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    persen_realisasi = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    persen_kelengkapan_dokumen = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    persen_spj_upload = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    persen_arsip = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=100, blank=True)
    percent_completed = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    bar = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.EXCEL_SEED)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tahun", "bulan_number", "satker_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["satker_code", "bulan_number", "tahun"],
                name="unique_monitoring_summary_period_satker",
            )
        ]
        indexes = [
            models.Index(fields=["tahun", "bulan_number"]),
            models.Index(fields=["satker_code", "tahun"]),
            models.Index(fields=["source"]),
        ]

    def __str__(self):
        return f"{self.satker_label} {self.bulan} {self.tahun}"


# =============================================================================
# SATKER MASTER - Unit Code to Official Satker Code Mapping
# =============================================================================

class SatkerMaster(models.Model):
    """
    Master table mapping between unit codes (4-digit) and official satker codes (6-digit).

    The 4-digit filename codes (KK_1300.xlsx) map to official 6-digit BPS satker codes.

    Example:
        unit_code: "1300"
        satker_code: "019937" (BPS Provinsi Sumatera Barat)
    """
    unit_code = models.CharField(
        max_length=4,
        unique=True,
        help_text="4-digit unit code from filename (KK_XXXX.xlsx)"
    )
    satker_code = models.CharField(
        max_length=6,
        unique=True,
        help_text="Official 6-digit satker code"
    )
    nama_satker = models.CharField(
        max_length=255,
        help_text="Full satker name"
    )
    jenis_unit = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ("PROVINSI", "Provinsi"),
            ("KABUPATEN", "Kabupaten/Kota"),
            ("KOTA", "Kota"),
        ]
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Satker Master"
        verbose_name_plural = "Satker Masters"
        ordering = ["satker_code"]

    def __str__(self):
        return f"{self.unit_code} -> {self.satker_code}: {self.nama_satker}"

    @classmethod
    def get_satker_code(cls, unit_code):
        """Get official satker_code from unit_code."""
        try:
            return cls.objects.get(unit_code=unit_code).satker_code
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_unit_code(cls, satker_code):
        """Get unit_code from official satker_code."""
        try:
            return cls.objects.get(satker_code=satker_code).unit_code
        except cls.DoesNotExist:
            return None

    @classmethod
    def get_satker_code_for_unit(cls, unit_code):
        """Alias for get_satker_code for backward compatibility."""
        return cls.get_satker_code(unit_code)

    @classmethod
    def get_unit_code_for_satker(cls, satker_code):
        """Alias for get_unit_code for backward compatibility."""
        return cls.get_unit_code(satker_code)


# Authoritative mapping seed data
SATKER_MAPPING = [
    ("1300", "019937", "BPS Provinsi Sumatera Barat", "PROVINSI"),
    ("1301", "636977", "BPS Kabupaten Kepulauan Mentawai", "KABUPATEN"),
    ("1302", "427981", "BPS Kabupaten Pesisir Selatan", "KABUPATEN"),
    ("1303", "019979", "BPS Kabupaten Solok", "KABUPATEN"),
    ("1304", "019983", "BPS Kabupaten Sijunjung", "KABUPATEN"),
    ("1305", "019990", "BPS Kabupaten Tanah Datar", "KABUPATEN"),
    ("1306", "019958", "BPS Kabupaten Padang Pariaman", "KABUPATEN"),
    ("1307", "428041", "BPS Kabupaten Agam", "KABUPATEN"),
    ("1308", "428063", "BPS Kabupaten Lima Puluh Kota", "KABUPATEN"),
    ("1309", "428057", "BPS Kabupaten Pasaman", "KABUPATEN"),
    ("1310", "667193", "BPS Kabupaten Solok Selatan", "KABUPATEN"),
    ("1311", "667172", "BPS Kabupaten Dharmasraya", "KABUPATEN"),
    ("1312", "667189", "BPS Kabupaten Pasaman Barat", "KABUPATEN"),
    ("1371", "019941", "BPS Kota Padang", "KOTA"),
    ("1372", "019962", "BPS Kota Solok", "KOTA"),
    ("1373", "428001", "BPS Kota Sawahlunto", "KOTA"),
    ("1374", "427990", "BPS Kota Padang Panjang", "KOTA"),
    ("1375", "428026", "BPS Kota Bukittinggi", "KOTA"),
    ("1376", "428032", "BPS Kota Payakumbuh", "KOTA"),
    ("1377", "668512", "BPS Kota Pariaman", "KOTA"),
]


# =============================================================================
# TRANSACTION PACKAGE
# =============================================================================

class TransactionPackage(models.Model):
    """
    Canonical transaction package representing one SPM transaction.

    This model serves as the single source of truth for a logical SPM package.
    It can be created from SP2D data, SPM uploads, or DRPP data, and progressively
    enriched as more documents are uploaded.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SP2D_READY = "SP2D_READY", "SP2D Siap"
        SPM_COMPLETE = "SPM_COMPLETE", "SPM Lengkap"
        DRPP_AVAILABLE = "DRPP_AVAILABLE", "DRPP Tersedia"
        COMPLETE = "COMPLETE", "Lengkap"

    # Official 6-digit satker_code - canonical financial identity
    satker_code = models.CharField(
        max_length=6,
        db_index=True,
        help_text="Official 6-digit BPS satker code (e.g., 019937)"
    )
    # Optional unit_code for reference (from filename KK_XXXX.xlsx)
    unit_code = models.CharField(
        max_length=4,
        blank=True,
        db_index=True,
        help_text="4-digit unit code from filename (e.g., 1300)"
    )
    tahun = models.PositiveSmallIntegerField(db_index=True)
    nomor_spm = models.CharField(max_length=100, db_index=True)

    # SP2D fields (from SP2D upload)
    no_sp2d = models.CharField(max_length=100, blank=True)
    tanggal_sp2d = models.DateField(null=True, blank=True)
    nilai_sp2d = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # SPM fields (from SPM document upload)
    tanggal_spm = models.DateField(null=True, blank=True)
    jenis_spm = models.CharField(max_length=100, blank=True)
    nilai_spm = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deskripsi = models.TextField(blank=True)

    # Status
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    # Source tracking
    has_sp2d = models.BooleanField(default=False)
    has_spm_document = models.BooleanField(default=False)
    has_drpp = models.BooleanField(default=False)
    sp2d_source = models.CharField(max_length=255, blank=True)
    spm_source = models.CharField(max_length=255, blank=True)

    # Counters
    drpp_count = models.PositiveIntegerField(default=0)
    kuitansi_count = models.PositiveIntegerField(default=0)
    dk_count = models.PositiveIntegerField(default=0)

    # Active parent context for DRPP uploads (stored as JSON-friendly text)
    active_drpp_parent_key = models.CharField(max_length=255, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Transaction Package"
        verbose_name_plural = "Transaction Packages"
        ordering = ["-created_at"]
        unique_together = [
            ["satker_code", "tahun", "nomor_spm"],
        ]
        indexes = [
            models.Index(fields=["satker_code", "tahun", "nomor_spm"]),
            models.Index(fields=["satker_code", "no_sp2d"]),
            models.Index(fields=["status", "satker_code"]),
            models.Index(fields=["satker_code", "tahun"]),
            models.Index(fields=["tahun"]),
            models.Index(fields=["unit_code", "tahun"]),
        ]

    def __str__(self):
        if self.unit_code:
            return f"{self.unit_code}/{self.satker_code}/{self.tahun}/{self.nomor_spm}"
        return f"{self.satker_code}/{self.tahun}/{self.nomor_spm}"

    @property
    def canonical_key(self):
        return (self.satker_code, self.tahun, self.nomor_spm)

    @classmethod
    def make_key(cls, satker_code: str, tahun: int, nomor_spm: str) -> str:
        return f"{satker_code}|{tahun}|{nomor_spm}"

    def update_status(self):
        if self.has_sp2d and self.has_spm_document and self.has_drpp:
            self.status = self.Status.COMPLETE
        elif self.has_sp2d and self.has_spm_document:
            self.status = self.Status.SPM_COMPLETE
        elif self.has_sp2d:
            self.status = self.Status.SP2D_READY
        else:
            self.status = self.Status.DRAFT


class ActiveParentSession(models.Model):
    """Stores the active SPM parent for DRPP uploads per user session."""

    session_key = models.CharField(max_length=40, db_index=True)
    user = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="active_parent_sessions",
    )
    transaction_package = models.ForeignKey(
        TransactionPackage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="active_sessions",
    )

    satker_code = models.CharField(max_length=32, blank=True)
    tahun = models.PositiveSmallIntegerField(null=True, blank=True)
    nomor_spm = models.CharField(max_length=100, blank=True)
    tanggal_spm = models.DateField(null=True, blank=True)
    jenis_spm = models.CharField(max_length=100, blank=True)

    selection_method = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ("EXPLICIT", "User explicitly selected"),
            ("AUTO_COMPATIBLE", "Auto-selected (compatible active)"),
            ("EVIDENCE_MATCH", "Matched trusted evidence"),
            ("FROZEN_PREVIEW", "Frozen from preview"),
        ],
    )
    selection_evidence = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["session_key", "user"]),
            models.Index(fields=["satker_code", "tahun", "nomor_spm"]),
        ]

    def __str__(self):
        return f"{self.satker_code}/{self.tahun}/{self.nomor_spm}"


class DRPPPreviewState(models.Model):
    """Stores the preview state for DRPP uploads."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        COMMITTED = "COMMITTED", "Committed"
        CANCELLED = "CANCELLED", "Cancelled"

    session_key = models.CharField(max_length=40, db_index=True)
    user = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="drpp_preview_states",
    )

    nomor_drpp = models.CharField(max_length=100)
    satker_code = models.CharField(max_length=32)
    tahun = models.PositiveSmallIntegerField(null=True, blank=True)

    frozen_parent_package = models.ForeignKey(
        TransactionPackage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drpp_previews_as_parent",
    )
    frozen_satker_code = models.CharField(max_length=32, blank=True)
    frozen_tahun = models.PositiveSmallIntegerField(null=True, blank=True)
    frozen_nomor_spm = models.CharField(max_length=100, blank=True)

    preview_data = models.JSONField(default=dict, blank=True)
    selection_conflict = models.BooleanField(default=False)
    conflict_message = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["session_key", "user", "status"]),
            models.Index(fields=["nomor_drpp", "satker_code"]),
            models.Index(fields=["frozen_satker_code", "frozen_tahun", "frozen_nomor_spm"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"Preview: {self.nomor_drpp} -> {self.frozen_satker_code}/{self.frozen_tahun}/{self.frozen_nomor_spm}"

    def is_frozen_parent_valid(self) -> bool:
        try:
            if not self.frozen_parent_package:
                return False
            return (
                self.frozen_parent_package.satker_code == self.frozen_satker_code
                and self.frozen_parent_package.tahun == self.frozen_tahun
                and self.frozen_parent_package.nomor_spm == self.frozen_nomor_spm
            )
        except TransactionPackage.DoesNotExist:
            return False
        except Exception:
            return False

    def get_frozen_parent_for_commit(self) -> Optional["TransactionPackage"]:
        """Get the frozen parent package for commit. Returns None if invalid."""
        if not self.is_frozen_parent_valid():
            return None
        try:
            return self.frozen_parent_package
        except TransactionPackage.DoesNotExist:
            return None


class TransactionProvenance(models.Model):
    """Tracks the provenance of data in the transaction system."""

    class SourceType(models.TextChoices):
        SP2D = "SP2D", "SP2D Import"
        SPM = "SPM", "SPM Document Upload"
        DRPP = "DRPP", "DRPP Upload"
        KK_EXCEL = "KK_EXCEL", "KK Excel Import"
        MIGRATION = "MIGRATION", "Data Migration"
        MANUAL = "MANUAL", "Manual Entry"
        LEGACY = "LEGACY", "Legacy Data"

    transaction_package = models.ForeignKey(
        TransactionPackage,
        on_delete=models.CASCADE,
        related_name="provenances",
        null=True,
        blank=True,
    )
    transaction_detail = models.ForeignKey(
        "dk.TransactionDetail",
        on_delete=models.CASCADE,
        related_name="provenances",
        null=True,
        blank=True,
    )

    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_filename = models.CharField(max_length=255, blank=True)
    source_batch_id = models.CharField(max_length=100, blank=True)
    source_upload_id = models.CharField(max_length=100, blank=True)

    original_satker = models.CharField(max_length=32, blank=True)
    original_tahun = models.PositiveSmallIntegerField(null=True, blank=True)
    original_nomor_spm = models.CharField(max_length=100, blank=True)
    original_nomor_drpp = models.CharField(max_length=100, blank=True)
    original_nomor_kwitansi = models.CharField(max_length=100, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Transaction Provenance"
        verbose_name_plural = "Transaction Provenances"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type"]),
            models.Index(fields=["source_filename"]),
            models.Index(fields=["transaction_package", "source_type"]),
        ]

    def __str__(self):
        return f"{self.get_source_type_display()} - {self.source_filename or 'N/A'}"

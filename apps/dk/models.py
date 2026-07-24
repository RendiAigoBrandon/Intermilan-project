from django.conf import settings
from django.db import models


class MasterAkun(models.Model):
    kode = models.CharField(max_length=32, unique=True)
    nama_akun = models.CharField(max_length=255)
    kategori = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kode"]
        indexes = [
            models.Index(fields=["kategori"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.kode} - {self.nama_akun}"


class TransactionDetail(models.Model):
    class StatusDetail(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        LENGKAP = "LENGKAP", "Lengkap"
        PERLU_REVIEW = "PERLU_REVIEW", "Perlu Review"
        MENUNGGU_SPM = "MENUNGGU_SPM", "Menunggu SPM"
        MENUNGGU_SP2D = "MENUNGGU_SP2D", "Menunggu SP2D"
        BELUM_SEIMBANG = "BELUM_SEIMBANG", "Belum Seimbang"
        SIAP_FINAL = "SIAP_FINAL", "Siap Final"
        FINAL = "FINAL", "Final"
        DIARSIPKAN = "DIARSIPKAN", "Diarsipkan"

    class DRPPStatus(models.TextChoices):
        BELUM_ADA = "BELUM_ADA", "Belum Ada"
        ADA = "ADA", "Ada"
        PERLU_DICEK = "PERLU_DICEK", "Perlu Dicek"
        COCOK = "COCOK", "Cocok"

    sp2d_raw = models.ForeignKey(
        "sp2d.SP2DRaw",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transaction_details",
    )
    satker_code = models.CharField(max_length=32, blank=True)
    akun = models.CharField(max_length=32)
    kategori = models.CharField(max_length=100, blank=True)
    bulan_sp2d = models.PositiveSmallIntegerField(null=True, blank=True)
    cara_pembayaran = models.CharField(max_length=100, blank=True)
    nomor_spm = models.CharField(max_length=100, blank=True)
    tanggal_spm = models.DateField(null=True, blank=True)
    jenis_spm = models.CharField(max_length=100, blank=True)
    no_kuitansi = models.CharField(max_length=100, blank=True)
    no_drpp = models.CharField(max_length=100, blank=True)
    deskripsi = models.TextField(blank=True)
    nilai_bruto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    nilai_netto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    pembebanan = models.CharField(max_length=255, blank=True)
    fp = models.CharField(max_length=100, blank=True)
    pph21 = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status_detail = models.CharField(max_length=20, choices=StatusDetail.choices, default=StatusDetail.DRAFT)
    drpp_status = models.CharField(max_length=20, choices=DRPPStatus.choices, default=DRPPStatus.BELUM_ADA)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_transaction_details",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["satker_code", "nomor_spm"]),
            models.Index(fields=["nomor_spm", "no_kuitansi"]),
            models.Index(fields=["nomor_spm", "no_drpp"]),
            models.Index(fields=["akun"]),
            models.Index(fields=["bulan_sp2d"]),
        ]

    @property
    def helper(self):
        return f"{self.akun or ''}{self.no_kuitansi or ''}"

    def __str__(self):
        label = self.no_kuitansi or self.no_drpp or self.nomor_spm or self.akun
        return f"{label} - {self.nilai_netto}"


class TransactionChangeLog(models.Model):
    class ChangeSource(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        PARSER = "PARSER", "Parser"
        IMPORT = "IMPORT", "Import"
        SYSTEM = "SYSTEM", "System"

    transaction = models.ForeignKey(TransactionDetail, on_delete=models.CASCADE, related_name="change_logs")
    field_name = models.CharField(max_length=100)
    old_value = models.TextField(blank=True, null=True)
    new_value = models.TextField(blank=True, null=True)
    change_source = models.CharField(max_length=50, choices=ChangeSource.choices, default=ChangeSource.MANUAL)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["transaction", "changed_at"]),
            models.Index(fields=["transaction", "field_name", "change_source"]),
        ]

from django.conf import settings
from django.db import models


class SP2DImportBatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    filename = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    tahun = models.PositiveSmallIntegerField(null=True, blank=True)
    bulan = models.PositiveSmallIntegerField(null=True, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    success_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sp2d_import_batches",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["tahun", "bulan"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.original_filename


class SP2DRaw(models.Model):
    class Status(models.TextChoices):
        PERLU_DETAIL = "PERLU_DETAIL", "Perlu Detail Akun"
        COCOK = "COCOK", "Cocok"
        TIDAK_COCOK = "TIDAK_COCOK", "Tidak Cocok"
        DRAFT = "DRAFT", "Draft"

    import_batch = models.ForeignKey(
        SP2DImportBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="raw_rows",
    )
    satker_code = models.CharField(max_length=32, blank=True)
    satker_name = models.CharField(max_length=255, blank=True)
    no_sp2d = models.CharField(max_length=100, blank=True)
    tanggal_selesai_sp2d = models.DateField(null=True, blank=True)
    tgl_sp2d = models.DateField(null=True, blank=True)
    mata_uang = models.CharField(max_length=20, blank=True)
    nilai_spm = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    potongan = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    nilai_sp2d = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    nomor_invoice = models.CharField(max_length=100, blank=True)
    tanggal_invoice = models.DateField(null=True, blank=True)
    jenis_spm = models.CharField(max_length=100, blank=True)
    jenis_sp2d = models.CharField(max_length=100, blank=True)
    deskripsi = models.TextField(blank=True)
    cek_akun = models.CharField(max_length=255, blank=True)
    nomor_spm_extracted = models.CharField(max_length=100, blank=True)
    bulan_sp2d = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PERLU_DETAIL)
    original_file = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_sp2d_rows",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["satker_code", "nomor_spm_extracted"]),
            models.Index(fields=["no_sp2d"]),
            models.Index(fields=["bulan_sp2d"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.no_sp2d or self.nomor_spm_extracted or f"SP2D #{self.pk}"

from django.conf import settings
from django.db import models


class ChecklistTemplate(models.Model):
    nama_dokumen = models.CharField(max_length=255)
    kategori = models.CharField(max_length=100, blank=True)
    wajib = models.BooleanField(default=True)
    urutan = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["urutan", "nama_dokumen"]
        indexes = [
            models.Index(fields=["kategori"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.nama_dokumen


class DocumentUpload(models.Model):
    transaction_detail = models.ForeignKey(
        "dk.TransactionDetail",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="document_uploads",
    )
    document_type = models.CharField(max_length=100)
    original_filename = models.CharField(max_length=255)
    stored_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="uploads/documents/%Y/%m/")
    file_hash = models.CharField(max_length=128, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=100, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    extracted_text = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["document_type"]),
            models.Index(fields=["file_hash"]),
        ]

    def __str__(self):
        return self.original_filename


class DocumentDriveLink(models.Model):
    class Status(models.TextChoices):
        AKTIF = "AKTIF", "Aktif"
        PERLU_DICEK = "PERLU_DICEK", "Perlu Dicek"
        TIDAK_AKTIF = "TIDAK_AKTIF", "Tidak Aktif"

    transaction_detail = models.ForeignKey(
        "dk.TransactionDetail",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drive_links",
    )
    satker_code = models.CharField(max_length=32, blank=True)
    nomor_spm = models.CharField(max_length=100, blank=True)
    no_kuitansi = models.CharField(max_length=100, blank=True)
    no_drpp = models.CharField(max_length=100, blank=True)
    jenis_dokumen = models.CharField(max_length=100, blank=True)
    nama_file = models.CharField(max_length=255, blank=True)
    google_drive_url = models.URLField(max_length=1000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AKTIF)
    catatan = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_drive_links",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["satker_code", "nomor_spm"]),
            models.Index(fields=["no_kuitansi"]),
            models.Index(fields=["no_drpp"]),
            models.Index(fields=["jenis_dokumen"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.nama_file or self.google_drive_url


class ChecklistStatus(models.Model):
    class Status(models.TextChoices):
        ADA = "ADA", "Ada"
        BELUM = "BELUM", "Belum"
        TIDAK_PERLU = "TIDAK_PERLU", "Tidak Perlu"

    transaction_detail = models.ForeignKey(
        "dk.TransactionDetail",
        on_delete=models.CASCADE,
        related_name="checklist_statuses",
    )
    nama_dokumen = models.CharField(max_length=255)
    wajib = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BELUM)
    dokumen_upload = models.ForeignKey(
        DocumentUpload,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checklist_statuses",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_checklists",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nama_dokumen"]
        unique_together = [("transaction_detail", "nama_dokumen")]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["wajib"]),
        ]

    def __str__(self):
        return f"{self.nama_dokumen} - {self.get_status_display()}"

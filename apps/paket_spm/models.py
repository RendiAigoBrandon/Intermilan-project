from django.conf import settings
from django.db import models


class PaketSPMUpload(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "Uploaded"
        PREVIEW = "PREVIEW", "Preview"
        COMMITTED = "COMMITTED", "Committed"
        FAILED = "FAILED", "Failed"

    zip_file = models.FileField(upload_to="uploads/paket_spm/%Y/%m/")
    original_filename = models.CharField(max_length=255)
    folder_path = models.CharField(max_length=500, blank=True)
    nomor_spm = models.CharField(max_length=100, blank=True)
    nomor_sp2d = models.CharField(max_length=100, blank=True)
    nomor_invoice = models.CharField(max_length=100, blank=True)
    satker_code = models.CharField(max_length=32, blank=True)
    tahun = models.PositiveSmallIntegerField(null=True, blank=True)
    bulan = models.PositiveSmallIntegerField(null=True, blank=True)
    jenis_spm_asli = models.CharField(max_length=100, blank=True)
    jenis_spm_label = models.CharField(max_length=100, blank=True)
    tanggal_spm = models.DateField(null=True, blank=True)
    nilai_spm = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_rincian_bruto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_rincian_netto = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    selisih = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    parsed_data = models.JSONField(null=True, blank=True, help_text="Menyimpan hasil raw extract/OCR agar tidak diproses berulang kali saat preview.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_paket_spm",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["satker_code", "nomor_spm"]),
            models.Index(fields=["tahun", "bulan"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.original_filename


class PaketSPMPreviewItem(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        MATCHED = "MATCHED", "Matched"
        PERLU_DICEK = "PERLU_DICEK", "Perlu Dicek"
        SKIP = "SKIP", "Skip"

    paket = models.ForeignKey(PaketSPMUpload, on_delete=models.CASCADE, related_name="preview_items")
    helper = models.CharField(max_length=255, blank=True)
    akun = models.CharField(max_length=32, blank=True)
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
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    catatan = models.TextField(blank=True)
    matched_transaction = models.ForeignKey(
        "dk.TransactionDetail",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="paket_spm_preview_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["paket", "id"]
        indexes = [
            models.Index(fields=["nomor_spm", "no_kuitansi"]),
            models.Index(fields=["nomor_spm", "no_drpp"]),
            models.Index(fields=["akun"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.no_kuitansi or self.no_drpp or self.nomor_spm or f"Preview #{self.pk}"

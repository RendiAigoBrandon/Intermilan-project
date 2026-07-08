from django.conf import settings
from django.db import models


class DRPPUpload(models.Model):
    class MatchStatus(models.TextChoices):
        BELUM_DIPROSES = "BELUM_DIPROSES", "Belum Diproses"
        COCOK = "COCOK", "Cocok"
        PERLU_DICEK = "PERLU_DICEK", "Perlu Dicek"
        KONFLIK = "KONFLIK", "Konflik"

    transaction_detail = models.ForeignKey(
        "dk.TransactionDetail",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drpp_uploads",
    )
    document_upload = models.ForeignKey(
        "documents.DocumentUpload",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drpp_uploads",
    )
    nomor_drpp = models.CharField(max_length=100, blank=True)
    nomor_drpp_norm = models.CharField(max_length=100, blank=True)
    tanggal_drpp = models.DateField(null=True, blank=True)
    jenis_spp = models.CharField(max_length=100, blank=True)
    bulan = models.PositiveSmallIntegerField(null=True, blank=True)
    tahun = models.PositiveSmallIntegerField(null=True, blank=True)
    satker_code = models.CharField(max_length=32, blank=True)
    nomor_spm = models.CharField(max_length=100, blank=True)
    total_jumlah = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    raw_text = models.TextField(blank=True)
    match_status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.BELUM_DIPROSES)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_drpps",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["satker_code", "nomor_spm"]),
            models.Index(fields=["nomor_drpp_norm"]),
            models.Index(fields=["tahun", "bulan"]),
            models.Index(fields=["match_status"]),
        ]

    def __str__(self):
        return self.nomor_drpp or f"DRPP #{self.pk}"


class DRPPItem(models.Model):
    class StatusVerifikasi(models.TextChoices):
        BELUM_DICEK = "BELUM_DICEK", "Belum Dicek"
        SESUAI = "SESUAI", "Sesuai"
        TIDAK_SESUAI = "TIDAK_SESUAI", "Tidak Sesuai"
        PERLU_REVIEW = "PERLU_REVIEW", "Perlu Review"

    drpp_upload = models.ForeignKey(DRPPUpload, on_delete=models.CASCADE, related_name="items")
    no_urut = models.PositiveIntegerField(null=True, blank=True)
    no_bukti = models.CharField(max_length=100, blank=True)
    no_bukti_norm = models.CharField(max_length=100, blank=True)
    tanggal_bukti = models.DateField(null=True, blank=True)
    penerima = models.CharField(max_length=255, blank=True)
    keperluan = models.TextField(blank=True)
    npwp = models.CharField(max_length=50, blank=True)
    akun = models.CharField(max_length=32, blank=True)
    jumlah = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status_verifikasi = models.CharField(
        max_length=20,
        choices=StatusVerifikasi.choices,
        default=StatusVerifikasi.BELUM_DICEK,
    )
    catatan = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["drpp_upload", "no_urut", "id"]
        indexes = [
            models.Index(fields=["no_bukti_norm"]),
            models.Index(fields=["akun"]),
            models.Index(fields=["status_verifikasi"]),
        ]

    def __str__(self):
        return self.no_bukti or f"Item DRPP #{self.pk}"


class DRPPMatch(models.Model):
    class StatusMatch(models.TextChoices):
        COCOK_OTOMATIS = "COCOK_OTOMATIS", "Cocok Otomatis"
        COCOK_MANUAL = "COCOK_MANUAL", "Cocok Manual"
        PERLU_DICEK = "PERLU_DICEK", "Perlu Dicek"
        KONFLIK = "KONFLIK", "Konflik"
        TIDAK_ADA_DI_DK = "TIDAK_ADA_DI_DK", "Tidak Ada di D_K"

    drpp_upload = models.ForeignKey(DRPPUpload, on_delete=models.CASCADE, related_name="matches")
    drpp_item = models.ForeignKey(DRPPItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="matches")
    transaction_detail = models.ForeignKey(
        "dk.TransactionDetail",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drpp_matches",
    )
    status_match = models.CharField(max_length=20, choices=StatusMatch.choices, default=StatusMatch.PERLU_DICEK)
    skor_match = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_manual = models.BooleanField(default=False)
    catatan = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["status_match"]),
            models.Index(fields=["is_manual"]),
        ]

    def __str__(self):
        return f"{self.drpp_upload} - {self.get_status_match_display()}"

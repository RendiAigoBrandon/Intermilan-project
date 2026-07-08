from django.db import models


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

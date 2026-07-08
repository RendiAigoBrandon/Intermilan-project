from django.contrib import admin

from .models import SP2DImportBatch, SP2DRaw


@admin.register(SP2DImportBatch)
class SP2DImportBatchAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "tahun", "bulan", "status", "total_rows", "uploaded_by", "uploaded_at")
    list_filter = ("status", "tahun", "bulan")
    search_fields = ("filename", "original_filename", "notes")


@admin.register(SP2DRaw)
class SP2DRawAdmin(admin.ModelAdmin):
    list_display = ("no_sp2d", "satker_code", "satker_name", "nomor_spm_extracted", "nilai_sp2d", "status")
    list_filter = ("status", "bulan_sp2d", "jenis_spm", "jenis_sp2d")
    search_fields = ("no_sp2d", "satker_code", "satker_name", "nomor_invoice", "nomor_spm_extracted")

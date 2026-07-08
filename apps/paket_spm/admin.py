from django.contrib import admin

from .models import PaketSPMPreviewItem, PaketSPMUpload


class PaketSPMPreviewItemInline(admin.TabularInline):
    model = PaketSPMPreviewItem
    extra = 0


@admin.register(PaketSPMUpload)
class PaketSPMUploadAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "satker_code", "nomor_spm", "nilai_spm", "status", "uploaded_at")
    list_filter = ("status", "tahun", "bulan")
    search_fields = ("original_filename", "satker_code", "nomor_spm")
    inlines = [PaketSPMPreviewItemInline]


@admin.register(PaketSPMPreviewItem)
class PaketSPMPreviewItemAdmin(admin.ModelAdmin):
    list_display = ("paket", "nomor_spm", "no_kuitansi", "no_drpp", "akun", "nilai_netto", "status")
    list_filter = ("status", "akun")
    search_fields = ("nomor_spm", "no_kuitansi", "no_drpp", "deskripsi")

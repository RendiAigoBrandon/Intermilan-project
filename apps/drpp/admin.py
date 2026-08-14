from django.contrib import admin

from .models import DRPPItem, DRPPMatch, DRPPSupportingAttachment, DRPPUpload


class DRPPItemInline(admin.TabularInline):
    model = DRPPItem
    extra = 0


class DRPPSupportingAttachmentInline(admin.TabularInline):
    model = DRPPSupportingAttachment
    extra = 0
    readonly_fields = ("document_upload", "archive_link", "created_at")


@admin.register(DRPPUpload)
class DRPPUploadAdmin(admin.ModelAdmin):
    list_display = ("nomor_drpp", "satker_code", "nomor_spm", "total_jumlah", "match_status", "uploaded_at")
    list_filter = ("match_status", "tahun", "bulan")
    search_fields = ("nomor_drpp", "nomor_drpp_norm", "satker_code", "nomor_spm")
    inlines = [DRPPItemInline, DRPPSupportingAttachmentInline]


@admin.register(DRPPItem)
class DRPPItemAdmin(admin.ModelAdmin):
    list_display = ("drpp_upload", "no_urut", "no_bukti", "akun", "jumlah", "status_verifikasi")
    list_filter = ("status_verifikasi", "akun")
    search_fields = ("no_bukti", "no_bukti_norm", "penerima", "keperluan", "npwp")


@admin.register(DRPPMatch)
class DRPPMatchAdmin(admin.ModelAdmin):
    list_display = ("drpp_upload", "drpp_item", "transaction_detail", "status_match", "skor_match", "is_manual")
    list_filter = ("status_match", "is_manual")
    search_fields = ("catatan",)


@admin.register(DRPPSupportingAttachment)
class DRPPSupportingAttachmentAdmin(admin.ModelAdmin):
    list_display = ("drpp_upload", "document_upload", "satker_code", "tahun", "nomor_drpp", "uploaded_by", "created_at")
    list_filter = ("satker_code", "tahun")
    search_fields = ("nomor_drpp", "nomor_drpp_norm", "document_upload__original_filename")

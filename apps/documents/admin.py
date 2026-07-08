from django.contrib import admin

from .models import ChecklistStatus, ChecklistTemplate, DocumentDriveLink, DocumentUpload


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(admin.ModelAdmin):
    list_display = ("nama_dokumen", "kategori", "wajib", "urutan", "is_active")
    list_filter = ("kategori", "wajib", "is_active")
    search_fields = ("nama_dokumen", "kategori")


@admin.register(DocumentUpload)
class DocumentUploadAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "document_type", "transaction_detail", "uploaded_by", "uploaded_at")
    list_filter = ("document_type", "mime_type")
    search_fields = ("original_filename", "stored_filename", "file_hash")


@admin.register(DocumentDriveLink)
class DocumentDriveLinkAdmin(admin.ModelAdmin):
    list_display = ("jenis_dokumen", "nama_file", "satker_code", "nomor_spm", "status", "created_at")
    list_filter = ("jenis_dokumen", "status", "satker_code")
    search_fields = ("nama_file", "google_drive_url", "nomor_spm", "no_kuitansi", "no_drpp")


@admin.register(ChecklistStatus)
class ChecklistStatusAdmin(admin.ModelAdmin):
    list_display = ("transaction_detail", "nama_dokumen", "wajib", "status", "updated_by", "updated_at")
    list_filter = ("status", "wajib")
    search_fields = ("nama_dokumen", "transaction_detail__nomor_spm")

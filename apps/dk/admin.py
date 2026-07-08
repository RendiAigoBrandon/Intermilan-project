from django.contrib import admin

from .models import MasterAkun, TransactionDetail


@admin.register(MasterAkun)
class MasterAkunAdmin(admin.ModelAdmin):
    list_display = ("kode", "nama_akun", "kategori", "is_active", "source")
    list_filter = ("kategori", "is_active", "source")
    search_fields = ("kode", "nama_akun", "kategori")


@admin.register(TransactionDetail)
class TransactionDetailAdmin(admin.ModelAdmin):
    list_display = ("nomor_spm", "no_kuitansi", "no_drpp", "satker_code", "akun", "nilai_netto", "status_detail")
    list_filter = ("status_detail", "drpp_status", "bulan_sp2d", "akun")
    search_fields = ("nomor_spm", "no_kuitansi", "no_drpp", "satker_code", "akun", "deskripsi")

from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "satker_code", "satker_name", "must_change_password", "updated_at")
    list_filter = ("role", "must_change_password")
    search_fields = ("user__username", "user__first_name", "user__last_name", "satker_code", "satker_name")

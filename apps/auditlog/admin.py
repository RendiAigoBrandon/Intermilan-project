from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "model_name", "object_id", "ip_address")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("description", "object_id", "user__username", "user_agent")
    readonly_fields = ("created_at",)

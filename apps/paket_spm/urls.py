from django.urls import path

from . import views


app_name = "paket_spm"

urlpatterns = [
    path("", views.paket_spm_list, name="list"),
    path("preview/", views.paket_spm_preview, name="preview"),
    path("drafts/", views.paket_spm_drafts, name="drafts"),
    path("change-active-parent/", views.change_active_parent, name="change_active_parent"),
    path("clear-active-parent/", views.clear_active_parent, name="clear_active_parent"),
]

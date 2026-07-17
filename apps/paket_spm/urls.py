from django.urls import path

from . import package_views, views


app_name = "paket_spm"

urlpatterns = [
    path("", package_views.paket_spm_list, name="list"),
    path("preview/", views.paket_spm_preview, name="preview"),
    path("drafts/", views.paket_spm_drafts, name="drafts"),
]

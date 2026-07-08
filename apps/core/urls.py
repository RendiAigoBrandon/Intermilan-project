from django.urls import path

from . import views


app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("audit-data/", views.audit_data, name="audit_data"),
    path("audit-data/export/", views.audit_data_export, name="audit_data_export"),
    path("monitoring/", views.monitoring, name="monitoring"),
    path("master-akun/", views.master_akun, name="master_akun"),
    path("akun/", views.akun_index, name="akun_index"),
    path("akun/<str:kode>/", views.akun_detail, name="akun_detail"),
    path("peraturan/", views.static_reference, {"kind": "peraturan"}, name="peraturan"),
    path("template/", views.static_reference, {"kind": "template"}, name="template"),
    path("panduan-aplikasi/", views.static_reference, {"kind": "panduan"}, name="panduan"),
]

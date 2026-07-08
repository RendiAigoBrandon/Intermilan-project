from django.urls import path

from . import views


app_name = "sp2d"

urlpatterns = [
    path("", views.sp2d_list, name="list"),
    path("preview/", views.sp2d_preview, name="preview"),
    path("inbox/<int:pk>/", views.sp2d_inbox_detail, name="inbox_detail"),
    path("kelengkapan/", views.sp2d_completeness, name="completeness"),
]

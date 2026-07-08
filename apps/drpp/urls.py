from django.urls import path

from . import views


app_name = "drpp"

urlpatterns = [
    path("", views.drpp_list, name="list"),
    path("preview/", views.drpp_preview, name="preview"),
]

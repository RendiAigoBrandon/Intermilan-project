from django.urls import path

from . import views


app_name = "drpp"

urlpatterns = [
    path("", views.drpp_list, name="list"),
    path("preview/", views.drpp_preview, name="preview"),
    path("change-active-parent/", views.change_active_parent, name="change_active_parent"),
    path("clear-active-parent/", views.clear_active_parent, name="clear_active_parent"),
]

from django.urls import path

from . import views
from .oauth_views import drive_oauth_authorize, drive_oauth_callback


app_name = "documents"

urlpatterns = [
    path("archive/", views.archive, name="archive"),
    path("", views.checklist_list, name="checklist"),
    path("<int:transaction_id>/", views.checklist_detail, name="checklist_detail"),
    # Google Drive OAuth
    path("drive/oauth/authorize/", drive_oauth_authorize, name="drive_oauth_authorize"),
    path("drive/oauth/callback/", drive_oauth_callback, name="drive_oauth_callback"),
]

from django.urls import path

from . import views


app_name = "documents"

urlpatterns = [
    path("", views.checklist_list, name="checklist"),
    path("<int:transaction_id>/", views.checklist_detail, name="checklist_detail"),
]

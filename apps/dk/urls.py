from django.urls import path

from . import views


app_name = "dk"

urlpatterns = [
    path("", views.transaction_list, name="transaction_list"),
    path("create/", views.transaction_create, name="transaction_create"),
    path("<int:pk>/edit/", views.transaction_edit, name="transaction_edit"),
    path("<int:pk>/duplicate/", views.transaction_duplicate, name="transaction_duplicate"),
    path("<int:pk>/archive/", views.transaction_archive, name="transaction_archive"),
]

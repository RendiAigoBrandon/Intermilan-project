from django.urls import path

from . import views


app_name = "dk"

urlpatterns = [
    path("", views.transaction_list, name="list"),
]

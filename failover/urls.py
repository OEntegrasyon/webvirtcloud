from django.urls import path

from . import views

app_name = "failover"

urlpatterns = [
    path("", views.failover, name="failover"),
    path("setup/<int:pk>/", views.setup, name="setup"),
    path("delete/<int:pk>/", views.delete, name="delete"),
    path("configure/<int:pk>/", views.configure, name="configure"),
    path("drbd_info/<int:pk>/", views.drbd_info, name="drbd_info"),
]
"""URL configuration for Between Nations."""

from django.contrib import admin
from django.urls import path

from intelligence import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("compare/<str:iso_a>/<str:iso_b>/", views.comparison, name="comparison"),
]

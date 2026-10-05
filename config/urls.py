"""URL configuration for Between Nations."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("intelligence.urls")),
]

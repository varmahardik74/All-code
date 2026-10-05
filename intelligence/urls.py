from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("compare/<str:iso_a>/<str:iso_b>/", views.comparison, name="comparison"),
]

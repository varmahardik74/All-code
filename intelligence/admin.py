from django.contrib import admin

from .models import AnalysisRun, Article, Country, CountryIndicator


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("name", "iso_code", "is_active", "created_at")
    search_fields = ("name", "iso_code")
    list_filter = ("is_active",)
    ordering = ("name",)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "country_a", "country_b", "published_at", "source_domain")
    search_fields = ("title", "url", "source_domain")


@admin.register(CountryIndicator)
class CountryIndicatorAdmin(admin.ModelAdmin):
    list_display = ("country", "indicator_code", "year", "value")
    search_fields = ("indicator_code", "indicator_name")


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("country_a", "country_b", "created_at")

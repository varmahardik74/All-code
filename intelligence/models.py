from django.db import models


class Country(models.Model):
    name = models.CharField(max_length=100, unique=True)
    iso_code = models.CharField(max_length=3, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.iso_code})"


class Article(models.Model):
    country_a = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="articles_as_a",
    )
    country_b = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="articles_as_b",
    )
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=500, db_index=True)
    source_domain = models.CharField(max_length=255)
    published_at = models.DateTimeField(db_index=True)
    tone = models.FloatField(null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["country_a", "country_b"]),
        ]

    def __str__(self):
        return self.title


class CountryIndicator(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="indicators",
    )
    indicator_code = models.CharField(max_length=64)
    indicator_name = models.CharField(max_length=255)
    value = models.FloatField()
    year = models.PositiveIntegerField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["country", "indicator_code", "year"],
                name="uniq_country_indicator_year",
            ),
        ]

    def __str__(self):
        return f"{self.country.iso_code} {self.indicator_code} {self.year}"


class AnalysisRun(models.Model):
    country_a = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="analysis_runs_as_a",
    )
    country_b = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="analysis_runs_as_b",
    )
    activity_score = models.FloatField(null=True, blank=True)
    narrative_drift_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    result_json = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["country_a", "country_b"]),
        ]

    def __str__(self):
        return f"{self.country_a.iso_code} vs {self.country_b.iso_code}"


def unordered_pair_q(country_a, country_b):
    """Filter matching articles stored in either country order (A-B = B-A)."""
    return (
        models.Q(country_a=country_a, country_b=country_b)
        | models.Q(country_a=country_b, country_b=country_a)
    )

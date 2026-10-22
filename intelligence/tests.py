"""Data-driven tests for the local-only Between Nations dashboard."""

import csv
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from intelligence.analysis import calculate_narrative_shift, classify_article, run_country_pair_analysis
from intelligence.models import AnalysisRun, Article, Country, CountryIndicator
from intelligence.seed import (
    INDICATORS_CSV_FILENAME,
    SEED_CSV_FILENAME,
    DatasetError,
    import_dataset,
)


def _read_article_csv():
    with (Path(settings.BASE_DIR) / SEED_CSV_FILENAME).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_indicator_csv():
    with (Path(settings.BASE_DIR) / INDICATORS_CSV_FILENAME).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _pair_rows(iso_codes):
    return [
        row
        for row in _read_article_csv()
        if set((row["country_a_iso"], row["country_b_iso"])) == set(iso_codes)
    ]


def _write_article_csv(pair_info):
    pair_key, iso_a, iso_b = pair_info.split(",")
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    with handle:
        handle.write(
            "article_id,pair_key,country_a_iso,country_b_iso,title,url,source_domain,"
            "published_date,published_at_utc,time_precision,tone,category,summary,notes\n"
        )
        handle.write(
            f"1,{pair_key},{iso_a},{iso_b},India and China hold diplomatic talks,"
            "https://example.com/seed-test,example.com,2026-10-02,,date_only,"
            "neutral,Diplomacy,Summary,Notes\n"
        )
    return Path(handle.name)


def _write_indicator_rows(rows):
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    with handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return Path(handle.name)


class DatasetImportTests(TestCase):
    def test_import_is_idempotent(self):
        import_dataset(out=StringIO())
        second = StringIO()
        import_dataset(out=second)
        self.assertEqual(Country.objects.filter(is_active=True).count(), 10)
        self.assertEqual(Article.objects.count(), len(_read_article_csv()))
        self.assertEqual(CountryIndicator.objects.count(), len(_read_indicator_csv()))
        self.assertIn("Rows inserted: 0", second.getvalue())
        article = Article.objects.get(url__contains="india-china-thaw")
        self.assertEqual((article.country_a.iso_code, article.country_b.iso_code), ("CHN", "IND"))
        self.assertEqual(article.category, "Diplomacy")

    def test_invalid_rows_are_rejected(self):
        path = _write_article_csv("CHN_FRA,FRA,CHN")
        with self.assertRaises(DatasetError):
            import_dataset(articles=path, out=StringIO())
        path.unlink(missing_ok=True)
        self.assertEqual(Article.objects.count(), 0)

    def test_stale_indicator_is_pruned(self):
        import_dataset(out=StringIO())
        usa = Country.objects.get(iso_code="USA")
        self.assertTrue(CountryIndicator.objects.filter(country=usa, indicator_code="NY.GDP.MKTP.CD").exists())
        rows = [
            row
            for row in _read_indicator_csv()
            if not (row["iso_code"] == "USA" and row["indicator_code"] == "NY.GDP.MKTP.CD")
        ]
        path = _write_indicator_rows(rows)
        try:
            import_dataset(indicators=path, out=StringIO())
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(CountryIndicator.objects.filter(country=usa, indicator_code="NY.GDP.MKTP.CD").exists())
        self.assertTrue(CountryIndicator.objects.filter(country=usa, indicator_code="SP.POP.TOTL").exists())


class AnalysisTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_dataset(out=StringIO())

    def test_classification(self):
        cases = [
            ("Ministers hold bilateral talks and diplomatic summit in Geneva", "Diplomacy"),
            ("Navy deploys guided missile destroyers for military exercise", "Security / Military"),
            ("New tariffs threaten bilateral trade and export quotas", "Trade / Economy"),
            ("Troops clash at disputed border following tense standoff", "Border / Conflict"),
            ("Treasury imposes fresh sanctions and trade embargo on entities", "Sanctions / Restrictions"),
            ("Nations sign strategic pact on semiconductor chip technology and AI", "Technology / Strategy"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_article(title), expected)

    def test_narrative_formula_and_guards(self):
        categories = ["Diplomacy", "Trade / Economy", "Security / Military"]
        self.assertEqual(calculate_narrative_shift(categories, categories)["shift_score"], 0.0)
        disjoint = calculate_narrative_shift(["Diplomacy", "Diplomacy"], ["Border / Conflict", "Border / Conflict"])
        self.assertEqual(disjoint["shift_score"], 100.0)
        self.assertEqual(disjoint["dominant_later_category"], "Border / Conflict")
        self.assertIsNone(calculate_narrative_shift([], ["Diplomacy"]))
        self.assertIsNone(calculate_narrative_shift(["Diplomacy"], []))

    def test_pair_honesty(self):
        india_china = run_country_pair_analysis(
            Country.objects.get(iso_code="IND"), Country.objects.get(iso_code="CHN")
        )
        self.assertEqual(india_china["metadata"]["article_count"], 7)
        self.assertEqual(len(_pair_rows({"IND", "CHN"})), 7)
        narrative = india_china["narrative_shift"]
        self.assertIsNotNone(narrative)
        self.assertEqual(narrative["earlier_article_count"] + narrative["later_article_count"], 7)
        daily = india_china["news_activity"]["daily_article_count"]
        self.assertEqual(len(daily), 19)
        self.assertAlmostEqual(india_china["news_activity"]["average_articles_per_day"], 7 / 19, places=2)
        self.assertEqual(india_china["metadata"]["source"], "local")
        self.assertEqual(india_china["metadata"]["window_start"], "2026-09-28")
        self.assertEqual(india_china["metadata"]["window_end"], "2026-10-16")
        self.assertTrue(
            AnalysisRun.objects.filter(
                country_a__iso_code="IND", country_b__iso_code="CHN"
            ).exists()
        )

        one_period = run_country_pair_analysis(
            Country.objects.get(iso_code="CHN"), Country.objects.get(iso_code="DEU")
        )
        self.assertEqual(one_period["metadata"]["article_count"], 2)
        self.assertIsNone(one_period["narrative_shift"])
        self.assertEqual(one_period["metadata"]["narrative_status"], "insufficient_data")

        no_data = run_country_pair_analysis(
            Country.objects.get(iso_code="CHN"), Country.objects.get(iso_code="ISR")
        )
        self.assertEqual(no_data["metadata"]["data_status"], "success_no_data")
        self.assertIsNone(no_data["news_activity"])
        self.assertIsNone(no_data["narrative_shift"])

    def test_curated_allowlist_and_order_independence(self):
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        expected = {row["url"] for row in _pair_rows({"IND", "CHN"})}
        non_curated = Article.objects.create(
            country_a=india,
            country_b=china,
            title="Uncorrelated talks",
            url="https://example.com/not-in-allowlist",
            source_domain="example.com",
            published_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
        forward = run_country_pair_analysis(india, china)
        reverse = run_country_pair_analysis(china, india)
        self.assertEqual({a["url"] for a in forward["articles"]}, expected)
        self.assertNotIn(non_curated.url, {a["url"] for a in forward["articles"]})
        self.assertEqual(
            {a["url"] for a in forward["articles"]},
            {a["url"] for a in reverse["articles"]},
        )


class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_dataset(out=StringIO())

    def test_pages_render(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "19-day historical snapshot")
        self.assertEqual(response.context["form"].fields["country_a"].queryset.count(), 10)

        response = self.client.get("/compare/USA/CHN/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "HISTORICAL SNAPSHOT")
        self.assertContains(response, "28 September–16 October 2026 (19 days)")

        response = self.client.get("/compare/CHN/DEU/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SHIFT SCORE")
        self.assertNotContains(response, "LATER DOMINANT")

        country = Country.objects.get(iso_code="USA")
        response = self.client.post("/", {"country_a": country.pk, "country_b": country.pk})
        self.assertContains(response, "Select two different countries.")

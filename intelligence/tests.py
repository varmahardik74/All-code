"""Comprehensive test suite for the local-only Between Nations dashboard.

The application never contacts an external API: it analyses curated articles
and locally exported country indicators from MySQL. Every test that touches
the analysis uses ``seed_data`` and the real project CSVs, so the assertions
are data-driven from the same files the live dashboard uses.
"""

import csv
import importlib
from datetime import date
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
import pandas as pd

from intelligence.models import AnalysisRun, Article, Country, CountryIndicator
from intelligence.analysis import (
    _daily_article_counts,
    calculate_narrative_shift,
    calculate_news_activity,
    classify_article,
    run_country_pair_analysis,
)
from intelligence.charts import (
    generate_activity_timeline_chart,
    generate_indicator_comparison_chart,
    generate_narrative_chart,
)
from intelligence.pairs import canonical_pair_key
from intelligence.seeds import (
    CANONICAL_INDICATORS,
    EARLIER_PERIOD_END,
    INDICATORS_CSV_FILENAME,
    SEED_CSV_FILENAME,
    SNAPSHOT_DAYS,
    SNAPSHOT_END,
    SNAPSHOT_START,
)


def _read_article_csv():
    seed_path = Path(settings.BASE_DIR) / SEED_CSV_FILENAME
    with seed_path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_indicator_csv():
    indicator_path = Path(settings.BASE_DIR) / INDICATORS_CSV_FILENAME
    with indicator_path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _pair_articles_expected(iso_codes):
    rows = [
        row
        for row in _read_article_csv()
        if set((row["country_a_iso"], row["country_b_iso"])) == set(iso_codes)
    ]
    earlier = [
        row
        for row in rows
        if SNAPSHOT_START <= date.fromisoformat(row["published_date"]) <= EARLIER_PERIOD_END
    ]
    later = [
        row
        for row in rows
        if EARLIER_PERIOD_END < date.fromisoformat(row["published_date"]) <= SNAPSHOT_END
    ]
    return rows, earlier, later


def _latest_indicator_value(iso_code, indicator_code):
    rows = [
        row
        for row in _read_indicator_csv()
        if row["iso_code"] == iso_code
        and row["indicator_code"] == indicator_code
        and (row.get("value") or "").strip()
    ]
    if not rows:
        return None
    return max(rows, key=lambda r: int(r["year"]))["value"]


def _latest_indicator_year(iso_code, indicator_code):
    rows = [
        row
        for row in _read_indicator_csv()
        if row["iso_code"] == iso_code and row["indicator_code"] == indicator_code
    ]
    if not rows:
        return None
    return int(max(rows, key=lambda r: int(r["year"]))["year"])


class CountryModelTests(TestCase):
    def test_name_and_iso_code_are_unique(self):
        Country.objects.create(name="United States", iso_code="USA")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Country.objects.create(name="United States of America", iso_code="USA")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Country.objects.create(name="United States", iso_code="UST")


class SeedDataCommandTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_data")
        call_command("seed_data")
        self.assertEqual(Country.objects.filter(is_active=True).count(), 10)
        self.assertTrue(Country.objects.filter(iso_code="USA", name="United States").exists())
        self.assertTrue(Country.objects.filter(iso_code="PAK", name="Pakistan", is_active=True).exists())
        self.assertTrue(Country.objects.filter(iso_code="ISR", name="Israel", is_active=True).exists())

    def test_removed_country_is_deactivated_without_deletion(self):
        legacy = Country.objects.create(name="France", iso_code="FRA", is_active=True)
        call_command("seed_data")
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertEqual(Country.objects.count(), 11)

    def test_seed_data_imports_valid_rows(self):
        article_rows = _read_article_csv()
        indicator_rows = _read_indicator_csv()
        unique_pairs = {
            canonical_pair_key(row["country_a_iso"], row["country_b_iso"])
            for row in article_rows
        }
        out = StringIO()
        call_command("seed_data", stdout=out)

        self.assertEqual(Article.objects.count(), len(article_rows))
        self.assertEqual(CountryIndicator.objects.count(), len(indicator_rows))
        self.assertIn(f"Rows read: {len(article_rows)}", out.getvalue())
        self.assertIn(f"Rows inserted: {len(article_rows)}", out.getvalue())
        self.assertIn(f"Unique pairs represented: {len(unique_pairs)}", out.getvalue())

        article = Article.objects.get(url__contains="india-china-thaw")
        self.assertEqual(article.category, "Diplomacy")
        self.assertEqual(article.tone, 0.0)
        self.assertEqual(article.country_a.iso_code, "CHN")
        self.assertEqual(article.country_b.iso_code, "IND")

    def test_seed_data_is_idempotent_for_articles_and_indicators(self):
        article_rows = _read_article_csv()
        indicator_rows = _read_indicator_csv()
        call_command("seed_data")
        out = StringIO()
        call_command("seed_data", stdout=out)
        self.assertEqual(Article.objects.count(), len(article_rows))
        self.assertEqual(CountryIndicator.objects.count(), len(indicator_rows))
        self.assertIn("Rows inserted: 0", out.getvalue())
        self.assertIn(f"Rows skipped as duplicates: {len(article_rows)}", out.getvalue())

    def test_indicators_preserve_exported_values(self):
        call_command("seed_data")
        usd_gdp = CountryIndicator.objects.get(
            country__iso_code="USA",
            indicator_code="NY.GDP.MKTP.CD",
            year=_latest_indicator_year("USA", "NY.GDP.MKTP.CD"),
        )
        expected = _latest_indicator_value("USA", "NY.GDP.MKTP.CD")
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(usd_gdp.value, float(expected), places=1)
        # No canonical code is missing for any active country.
        active = Country.objects.filter(is_active=True)
        for country in active:
            self.assertEqual(
                CountryIndicator.objects.filter(country=country).values_list("indicator_code", flat=True).distinct().count(),
                len(CANONICAL_INDICATORS),
            )

    def test_invalid_country_code_is_rejected(self):
        path = self._write_article_csv("CHN_FRA,FRA,CHN")
        with self.assertRaises(CommandError):
            call_command("seed_data", articles=str(path))
        self.assertEqual(Article.objects.count(), 0)
        path.unlink(missing_ok=True)

    def test_invalid_pair_key_is_rejected(self):
        path = self._write_article_csv("CHN_USA,CHN,IND")
        with self.assertRaises(CommandError):
            call_command("seed_data", articles=str(path))
        self.assertEqual(Article.objects.count(), 0)
        path.unlink(missing_ok=True)

    def test_country_order_does_not_matter_for_duplicate_detection(self):
        path = self._write_article_csv("CHN_IND,IND,CHN")
        call_command("seed_data", articles=str(path))
        call_command("seed_data", articles=str(path))
        self.assertEqual(Article.objects.count(), 1)
        article = Article.objects.get()
        self.assertEqual((article.country_a.iso_code, article.country_b.iso_code), ("CHN", "IND"))
        path.unlink(missing_ok=True)

    def test_missing_indicator_values_stay_missing(self):
        article_path = self._write_article_csv("CHN_IND,IND,CHN")
        indicator_path = self._write_indicator_csv("IND")
        call_command("seed_data", articles=str(article_path), indicators=str(indicator_path))
        self.assertEqual(CountryIndicator.objects.filter(country__iso_code="IND").count(), 1)
        row = CountryIndicator.objects.get(country__iso_code="IND", indicator_code="NY.GDP.MKTP.CD")
        self.assertEqual(row.year, 2025)
        self.assertAlmostEqual(row.value, float("3900000000000"), places=1)
        article_path.unlink(missing_ok=True)
        indicator_path.unlink(missing_ok=True)

    def test_prunes_indicator_code_absent_from_authoritative_csv(self):
        call_command("seed_data")
        usa = Country.objects.get(iso_code="USA")
        self.assertTrue(CountryIndicator.objects.filter(country=usa, indicator_code="NY.GDP.MKTP.CD").exists())
        rows = [row for row in _read_indicator_csv() if not (row["iso_code"] == "USA" and row["indicator_code"] == "NY.GDP.MKTP.CD")]
        path = self._write_indicator_rows(rows)
        try:
            call_command("seed_data", indicators=str(path))
            self.assertFalse(CountryIndicator.objects.filter(country=usa, indicator_code="NY.GDP.MKTP.CD").exists())
        finally:
            path.unlink(missing_ok=True)

    def test_prunes_indicator_code_with_only_blank_csv_values(self):
        call_command("seed_data")
        usa = Country.objects.get(iso_code="USA")
        rows = _read_indicator_csv()
        for row in rows:
            if row["iso_code"] == "USA" and row["indicator_code"] == "NY.GDP.MKTP.CD":
                row["value"] = ""
        path = self._write_indicator_rows(rows)
        try:
            call_command("seed_data", indicators=str(path))
            self.assertFalse(CountryIndicator.objects.filter(country=usa, indicator_code="NY.GDP.MKTP.CD").exists())
        finally:
            path.unlink(missing_ok=True)

    def _write_indicator_rows(self, rows):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
        with handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return Path(handle.name)

    def _write_article_csv(self, pair_info):
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

    def _write_indicator_csv(self, iso):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
        with handle:
            handle.write(
                "iso_code,country_name,indicator_code,indicator_name,value,year,fetched_at,source,extra\n"
            )
            handle.write(
                f"{iso},India,NY.GDP.MKTP.CD,GDP (current US$),3900000000000,2025,2026-10-09T00:00:00Z,World Bank,\n"
            )
            handle.write(
                f"{iso},India,SP.POP.TOTL,Population total,,2025,2026-10-09T00:00:00Z,World Bank,\n"
            )
        return Path(handle.name)


class HomePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data")

    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Between Nations")
        self.assertContains(response, "intelligence/css/styles.css")
        self.assertContains(response, "19-day historical snapshot")

    def test_dropdowns_contain_seeded_countries(self):
        response = self.client.get("/")
        self.assertContains(response, "United States")
        self.assertContains(response, "Pakistan")
        self.assertContains(response, "Israel")
        self.assertNotContains(response, "Indonesia")
        self.assertNotContains(response, "France")
        self.assertEqual(response.context["form"].fields["country_a"].queryset.count(), 10)
        self.assertEqual(response.context["form"].fields["country_b"].queryset.count(), 10)

    def test_same_country_selection_is_rejected(self):
        country = Country.objects.get(iso_code="USA")
        response = self.client.post(
            "/",
            {"country_a": country.pk, "country_b": country.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select two different countries.")

    def test_successful_comparison_renders_dashboard(self):
        usa = Country.objects.get(iso_code="USA")
        chn = Country.objects.get(iso_code="CHN")
        response = self.client.post(
            "/",
            {"country_a": usa.pk, "country_b": chn.pk},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "United States")
        self.assertContains(response, "China")
        self.assertContains(response, "HISTORICAL SNAPSHOT")
        self.assertContains(response, "28 September–16 October 2026 (19 days)")


# =========================================================================
# Classification, Narrative Shift, News Activity & Charts
# =========================================================================

class NarrativeServiceTests(TestCase):
    def test_article_classification(self):
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

    def test_narrative_shift_formula(self):
        base = ["Diplomacy", "Trade / Economy", "Security / Military"]
        recent = ["Diplomacy", "Trade / Economy", "Security / Military"]
        res_identical = calculate_narrative_shift(base, recent)
        self.assertEqual(res_identical["shift_score"], 0.0)

        base_disjoint = ["Diplomacy", "Diplomacy"]
        recent_disjoint = ["Border / Conflict", "Border / Conflict"]
        res_disjoint = calculate_narrative_shift(base_disjoint, recent_disjoint)
        self.assertEqual(res_disjoint["shift_score"], 100.0)
        self.assertEqual(res_disjoint["dominant_later_category"], "Border / Conflict")
        self.assertEqual(res_disjoint["largest_positive_shift"], "Border / Conflict")
        self.assertEqual(res_disjoint["largest_negative_shift"], "Diplomacy")

    def test_narrative_returns_none_when_earlier_period_is_empty(self):
        self.assertIsNone(calculate_narrative_shift([], ["Diplomacy"]))

    def test_narrative_returns_none_when_later_period_is_empty(self):
        self.assertIsNone(calculate_narrative_shift(["Diplomacy"], []))

    def test_narrative_distribution_pandas(self):
        base = ["Diplomacy"] * 8 + ["Trade / Economy"] * 2
        recent = ["Diplomacy"] * 4 + ["Security / Military"] * 6
        res = calculate_narrative_shift(base, recent)
        self.assertGreater(res["shift_score"], 0.0)
        self.assertEqual(res["dominant_later_category"], "Security / Military")


class ScoringServiceTests(TestCase):
    def test_daily_article_counts_cover_all_snapshot_days(self):
        articles = [
            MagicMock(published_at=timezone.make_aware(pd.Timestamp("2026-10-01").to_pydatetime())),
            MagicMock(published_at=timezone.make_aware(pd.Timestamp("2026-10-01").to_pydatetime())),
            MagicMock(published_at=timezone.make_aware(pd.Timestamp("2026-10-03").to_pydatetime())),
        ]

        counts = _daily_article_counts(articles)

        self.assertEqual(len(counts), 19)
        self.assertEqual(sum(counts), 3.0)
        self.assertEqual(max(counts), 2.0)
        self.assertEqual(counts[3], 2.0)  # 1 October
        self.assertEqual(counts[5], 1.0)  # 3 October

    def test_activity_average_includes_zero_days(self):
        res = calculate_news_activity([1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(res["average_articles_per_day"], 4.0)
        self.assertEqual(res["peak_day"], 7)
        self.assertEqual(res["peak_article_count"], 7.0)


class ChartGenerationTests(TestCase):
    def test_all_charts_return_valid_png_bytes(self):
        df = pd.DataFrame({
            "date": pd.date_range(SNAPSHOT_START, periods=19, freq="D"),
            "volume": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        })
        png_timeline = generate_activity_timeline_chart(df, "India", "China")
        self.assertTrue(png_timeline.startswith(b"\x89PNG"))

        narrative_res = {
            "shift_score": 45.0,
            "categories": [
                {"category": "Diplomacy", "earlier_share": 0.4, "later_share": 0.2},
                {"category": "Security / Military", "earlier_share": 0.2, "later_share": 0.5},
            ],
        }
        png_narrative = generate_narrative_chart(narrative_res, "India", "China")
        self.assertTrue(png_narrative.startswith(b"\x89PNG"))

        ind_a = {"NY.GDP.MKTP.KD.ZG": {"value": 7.2}, "FP.CPI.TOTL.ZG": {"value": 5.4}}
        ind_b = {"NY.GDP.MKTP.KD.ZG": {"value": 5.0}, "FP.CPI.TOTL.ZG": {"value": 2.1}}
        png_ind = generate_indicator_comparison_chart(ind_a, ind_b, "India", "China")
        self.assertTrue(png_ind.startswith(b"\x89PNG"))


# =========================================================================
# Local-only snapshot behaviour
# =========================================================================

class LocalDataSnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data")

    def test_analysis_uses_curated_urls_only(self):
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        expected_rows = [row for row in _read_article_csv() if set((row["country_a_iso"], row["country_b_iso"])) == {"IND", "CHN"}]
        non_curated = Article.objects.create(
            country_a=india,
            country_b=china,
            title="India and China hold uncorrelated talks",
            url="https://example.com/not-in-allowlist",
            source_domain="example.com",
            published_at=timezone.make_aware(pd.Timestamp("2026-10-05").to_pydatetime()),
        )
        result = run_country_pair_analysis(india, china)
        self.assertEqual(result["metadata"]["article_count"], len(expected_rows))
        self.assertEqual(
            {article["url"] for article in result["articles"]},
            {row["url"] for row in expected_rows},
        )
        self.assertNotIn(non_curated.url, {article["url"] for article in result["articles"]})

    def test_unordered_pair_equality(self):
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        forward = run_country_pair_analysis(india, china)
        reverse = run_country_pair_analysis(china, india)
        self.assertEqual(forward["metadata"]["article_count"], reverse["metadata"]["article_count"])
        self.assertEqual(
            {article["url"] for article in forward["articles"]},
            {article["url"] for article in reverse["articles"]},
        )

    def test_priority_pair_counts(self):
        expected = {
            ("IND", "CHN"): 7,
            ("ISR", "IRN"): 8,
            ("IND", "PAK"): 6,
            ("IRN", "USA"): 10,
            ("USA", "CHN"): 8,
            ("DEU", "RUS"): 7,
            ("GBR", "RUS"): 6,
            ("GBR", "USA"): 5,
        }
        for (iso_a, iso_b), count in expected.items():
            with self.subTest(pair=f"{iso_a}-{iso_b}"):
                country_a = Country.objects.get(iso_code=iso_a)
                country_b = Country.objects.get(iso_code=iso_b)
                rows, _, _ = _pair_articles_expected({iso_a, iso_b})
                result = run_country_pair_analysis(country_a, country_b)
                self.assertEqual(len(rows), count)
                self.assertEqual(result["metadata"]["article_count"], count)

    def test_news_activity_uses_nineteen_day_denominator(self):
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        rows, _, _ = _pair_articles_expected({"IND", "CHN"})
        result = run_country_pair_analysis(india, china)
        daily = result["news_activity"]["daily_article_count"]
        self.assertEqual(len(daily), 19)
        self.assertEqual(sum(daily), len(rows))
        self.assertAlmostEqual(
            result["news_activity"]["average_articles_per_day"],
            len(rows) / 19,
            places=2,
        )

    def test_narrative_shift_periods_match_snapshot_split(self):
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        rows, earlier, later = _pair_articles_expected({"IND", "CHN"})
        result = run_country_pair_analysis(india, china)
        narrative = result["narrative_shift"]
        self.assertEqual(len(rows), result["metadata"]["article_count"])
        self.assertEqual(narrative["earlier_article_count"], len(earlier))
        self.assertEqual(narrative["later_article_count"], len(later))
        self.assertEqual(narrative["earlier_article_count"] + narrative["later_article_count"], len(rows))

    def test_narrative_shift_is_unavailable_when_pair_has_only_earlier_articles(self):
        china = Country.objects.get(iso_code="CHN")
        germany = Country.objects.get(iso_code="DEU")
        rows, earlier, later = _pair_articles_expected({"CHN", "DEU"})
        self.assertEqual(len(rows), 2)
        self.assertTrue(earlier)
        self.assertFalse(later)
        result = run_country_pair_analysis(china, germany)
        self.assertIsNone(result["narrative_shift"])
        self.assertEqual(result["metadata"]["narrative_status"], "insufficient_data")

    def test_dashboard_hides_shift_metrics_for_one_period_pair(self):
        response = self.client.get("/compare/CHN/DEU/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SHIFT SCORE")
        self.assertNotContains(response, "LATER DOMINANT")

    def test_uncovered_pair_is_honest_no_data(self):
        china = Country.objects.get(iso_code="CHN")
        israel = Country.objects.get(iso_code="ISR")
        self.assertEqual(len(_pair_articles_expected({"CHN", "ISR"})[0]), 0)
        result = run_country_pair_analysis(china, israel)
        self.assertEqual(result["metadata"]["data_status"], "success_no_data")
        self.assertEqual(result["metadata"]["article_count"], 0)
        self.assertIsNone(result["news_activity"])
        self.assertIsNone(result["narrative_shift"])

    def test_indicators_are_local_exported_values(self):
        usa = Country.objects.get(iso_code="USA")
        china = Country.objects.get(iso_code="CHN")
        result = run_country_pair_analysis(usa, china)
        indicators = result["indicators"]
        expected = _latest_indicator_value("USA", "NY.GDP.MKTP.CD")
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(
            indicators["USA"]["NY.GDP.MKTP.CD"]["value"],
            float(expected),
            places=1,
        )
        # Every canonical code is present for every active country.
        for iso in ("USA", "CHN"):
            for code in CANONICAL_INDICATORS:
                self.assertIn(code, indicators[iso])
                self.assertIsNotNone(indicators[iso][code]["value"])

    def test_analysis_run_is_recorded(self):
        usa = Country.objects.get(iso_code="USA")
        chn = Country.objects.get(iso_code="CHN")
        run_country_pair_analysis(usa, chn)
        self.assertTrue(AnalysisRun.objects.filter(country_a=usa, country_b=chn).exists())
        run = AnalysisRun.objects.get(country_a=usa, country_b=chn)
        self.assertEqual(run.result_json["metadata"]["data_status"], "success_with_data")
        self.assertEqual(run.result_json["metadata"]["source"], "local")
        self.assertEqual(run.result_json["metadata"]["analysis_window_days"], 19)

    def test_metadata_has_no_live_mode_keys(self):
        usa = Country.objects.get(iso_code="USA")
        chn = Country.objects.get(iso_code="CHN")
        result = run_country_pair_analysis(usa, chn)
        self.assertNotIn("gdelt_status", result["metadata"])
        self.assertNotIn("geopolitical_data_available", result["metadata"])
        self.assertNotIn("news_data_mode", result["metadata"])
        self.assertEqual(result["metadata"]["window_start"], "2026-09-28")
        self.assertEqual(result["metadata"]["window_end"], "2026-10-16")


class LocalOnlyArchitectureTests(TestCase):
    def test_extended_snapshot_and_appended_article_rows(self):
        rows = _read_article_csv()
        self.assertEqual(SNAPSHOT_DAYS, 19)
        self.assertEqual(len(rows), 108)
        self.assertEqual([int(row["article_id"]) for row in rows], list(range(1, 109)))
        added = [row for row in rows if row["published_date"] >= "2026-10-10"]
        self.assertEqual(len(added), 15)
        self.assertTrue(all("2026-10-10" <= row["published_date"] <= "2026-10-16" for row in added))
        self.assertTrue(any(row["published_date"] == "2026-10-16" for row in added))
        self.assertEqual(len({row["url"] for row in rows}), len(rows))

    def test_external_api_modules_have_been_removed(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("intelligence.gdelt")
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("intelligence.worldbank")

    def test_seed_csv_has_no_duplicate_urls(self):
        urls = [row["url"] for row in _read_article_csv()]
        self.assertEqual(len(urls), len(set(urls)))

    def test_seed_csv_dates_fit_snapshot_window(self):
        for row in _read_article_csv():
            day = pd.Timestamp(row["published_date"]).date()
            self.assertGreaterEqual(day, SNAPSHOT_START)
            self.assertLessEqual(day, SNAPSHOT_END)
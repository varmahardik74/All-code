"""Comprehensive test suite for Between Nations.

Covers:
- Phase 1: Database models, seeding, dropdowns, validation, library checks.
- Phase 2: GDELT parsing, World Bank parsing, deterministic article classification,
  Narrative Shift formula, News Activity calculations, chart generation,
  and duplicate prevention. All external API tests are strictly mocked.
"""

from io import BytesIO
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
import numpy as np
import pandas as pd

from intelligence.models import AnalysisRun, Article, Country, CountryIndicator
from intelligence.services.analysis import _daily_article_counts, run_country_pair_analysis
from intelligence.services.charts import (
    generate_activity_timeline_chart,
    generate_indicator_comparison_chart,
    generate_narrative_chart,
)
from intelligence.services.gdelt import (
    GDELTError,
    GDELTParseError,
    GDELTRateLimitError,
    _request_gdelt,
    get_recent_bilateral_articles,
    is_bilateral_relevant,
    store_articles,
)
from intelligence.services.library_check import check_matplotlib, check_numpy, check_pandas
from intelligence.services.narrative import calculate_narrative_shift, classify_article
from intelligence.services.scoring import calculate_news_activity
from intelligence.services.worldbank import (
    COUNTRY_INDICATORS,
    fetch_all_country_indicators,
    fetch_country_indicator_from_api,
)


class CountryModelTests(TestCase):
    def test_name_and_iso_code_are_unique(self):
        Country.objects.create(name="United States", iso_code="USA")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Country.objects.create(name="United States of America", iso_code="USA")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Country.objects.create(name="United States", iso_code="UST")


class SeedCountriesCommandTests(TestCase):
    def test_seed_is_idempotent(self):
        call_command("seed_countries")
        call_command("seed_countries")
        self.assertEqual(Country.objects.count(), 20)
        self.assertTrue(Country.objects.filter(iso_code="USA", name="United States").exists())
        self.assertTrue(Country.objects.filter(iso_code="IDN", name="Indonesia").exists())


class HomePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_countries")

    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Between Nations")
        self.assertContains(response, "intelligence/css/styles.css")

    def test_dropdowns_contain_seeded_countries(self):
        response = self.client.get("/")
        self.assertContains(response, "United States")
        self.assertContains(response, "Indonesia")
        self.assertEqual(response.context["form"].fields["country_a"].queryset.count(), 20)
        self.assertEqual(response.context["form"].fields["country_b"].queryset.count(), 20)

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


class LibraryCheckTests(TestCase):
    def test_numpy_library_check(self):
        result = check_numpy()
        self.assertEqual(result["mean"], 6.0)
        self.assertGreater(result["std"], 0)

    def test_pandas_library_check(self):
        result = check_pandas()
        self.assertEqual(result["sorted_names"][0], "China")
        self.assertEqual(result["row_count"], 3)
        self.assertAlmostEqual(result["mean_score"], 3.333333, places=4)

    def test_matplotlib_library_check(self):
        result = check_matplotlib()
        self.assertEqual(result["format"], "png")
        self.assertGreater(result["size_bytes"], 0)
        self.assertIsInstance(result["buffer"], BytesIO)


# =========================================================================
# GDELT, World Bank, Narrative Shift, News Activity & Charts
# =========================================================================

class GDELTServiceTests(TestCase):
    def setUp(self):
        self.country_a = Country.objects.create(name="India", iso_code="IND")
        self.country_b = Country.objects.create(name="China", iso_code="CHN")

    @patch("requests.get")
    def test_gdelt_articlelist_response_parsing(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"articles": [{"title": "India and China hold diplomatic talks", "url": "https://example.com/article", "domain": "example.com", "seendate": "20261004T120000Z"}]}'
        mock_response.json.return_value = {"articles": [{
            "title": "India and China hold diplomatic talks",
            "url": "https://example.com/article",
            "domain": "example.com",
            "seendate": "20261004T120000Z",
        }]}
        mock_get.return_value = mock_response

        articles = get_recent_bilateral_articles(self.country_a, self.country_b)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["url"], "https://example.com/article")
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.kwargs["params"]["mode"], "ArtList")
        self.assertEqual(mock_get.call_args.kwargs["params"]["maxrecords"], 150)

    @patch("requests.get")
    def test_empty_gdelt_result(self, mock_get):
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json"}, text="{}")
        mock_response.json.return_value = {}
        mock_get.return_value = mock_response

        articles = get_recent_bilateral_articles(self.country_a, self.country_b)
        self.assertEqual(articles, [])

    def test_bilateral_relevance_filter_and_aliases(self):
        self.assertTrue(is_bilateral_relevant("India and China hold bilateral border talks", self.country_a, self.country_b))
        self.assertTrue(is_bilateral_relevant("New Delhi and Beijing discuss trade relations", self.country_a, self.country_b))
        self.assertFalse(is_bilateral_relevant("Chinese automobile market share rises", self.country_a, self.country_b))

    @patch("requests.get")
    def test_irrelevant_articles_are_rejected(self, mock_get):
        response = MagicMock(status_code=200, text="{}")
        response.json.return_value = {"articles": [
            {"title": "India and China hold diplomatic talks", "url": "https://example.com/relevant", "domain": "example.com", "seendate": "20261004T120000Z"},
            {"title": "Chinese automobile market share rises", "url": "https://example.com/irrelevant", "domain": "example.com", "seendate": "20261004T120000Z"},
        ]}
        mock_get.return_value = response
        articles = get_recent_bilateral_articles(self.country_a, self.country_b)
        self.assertEqual([article["url"] for article in articles], ["https://example.com/relevant"])

    @patch("requests.get")
    def test_invalid_gdelt_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html>Internal Server Error</html>"
        mock_get.return_value = mock_response

        with self.assertRaises(GDELTError):
            get_recent_bilateral_articles(self.country_a, self.country_b)

    @patch("requests.get")
    def test_invalid_gdelt_json_shape(self, mock_get):
        mock_response = MagicMock(status_code=200, headers={"Content-Type": "application/json"}, text="[]")
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        with self.assertRaises(GDELTError):
            get_recent_bilateral_articles(self.country_a, self.country_b)

    @patch("intelligence.services.gdelt.time.sleep")
    @patch("requests.get")
    def test_rate_limit_retry_honors_retry_after(self, mock_get, mock_sleep):
        limited = MagicMock(status_code=429, headers={"Retry-After": "2"}, text="rate limited")
        success = MagicMock(status_code=200, headers={}, text="{}")
        success.json.return_value = {}
        mock_get.side_effect = [limited, success]
        self.assertEqual(_request_gdelt({"query": "test"}), {})
        mock_sleep.assert_called_once_with(2.0)

    @patch("intelligence.services.gdelt.time.sleep")
    @patch("requests.get")
    def test_rate_limit_retry_exhausted(self, mock_get, mock_sleep):
        mock_get.return_value = MagicMock(status_code=429, headers={"Retry-After": "1"}, text="rate limited")
        with self.assertRaises(GDELTRateLimitError):
            _request_gdelt({"query": "test"})
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    def test_duplicate_article_prevention(self):
        sample_articles = [
            {
                "title": "India and China Hold High-Level Border Talks",
                "url": "https://example.com/article1",
                "source_domain": "example.com",
                "published_at": "2026-10-01T12:00:00Z",
                "tone": -1.5,
            },
            {
                "title": "Bilateral Trade Expands Between Delhi and Beijing",
                "url": "https://example.com/article2",
                "source_domain": "example.com",
                "published_at": "2026-10-02T14:00:00Z",
                "tone": 2.1,
            },
        ]
        created_first = store_articles(self.country_a, self.country_b, sample_articles)
        self.assertEqual(created_first, 2)
        self.assertEqual(Article.objects.count(), 2)

        # Re-storing identical articles must not insert duplicates
        created_second = store_articles(self.country_a, self.country_b, sample_articles)
        self.assertEqual(created_second, 0)
        self.assertEqual(Article.objects.count(), 2)


class WorldBankServiceTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="India", iso_code="IND")

    @patch("requests.get")
    def test_worldbank_response_parsing(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 20, "total": 2},
            [
                {"indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"}, "date": "2025", "value": 3950000000000.0},
                {"indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"}, "date": "2024", "value": 3600000000000.0},
            ],
        ]
        mock_get.return_value = mock_resp

        result = fetch_country_indicator_from_api("IND", "NY.GDP.MKTP.CD")
        self.assertIsNotNone(result)
        self.assertEqual(result["value"], 3950000000000.0)
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["country_iso"], "IND")

    @patch("requests.get")
    def test_missing_worldbank_indicator(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 20, "total": 2},
            [
                {"indicator": {"id": "MS.MIL.XPND.GD.ZS", "value": "Military Expenditure"}, "date": "2025", "value": None},
                {"indicator": {"id": "MS.MIL.XPND.GD.ZS", "value": "Military Expenditure"}, "date": "2024", "value": None},
            ],
        ]
        mock_get.return_value = mock_resp

        result = fetch_country_indicator_from_api("IND", "MS.MIL.XPND.GD.ZS")
        # Must be None, not 0
        self.assertIsNone(result)

    @patch("intelligence.services.worldbank.fetch_country_indicator_from_api")
    def test_indicator_storage(self, mock_fetch):
        mock_fetch.return_value = {
            "indicator_code": "NY.GDP.MKTP.CD",
            "indicator_name": "GDP (current US$)",
            "value": 3900000000000.0,
            "year": 2025,
            "country_iso": "IND",
        }

        indicators = fetch_all_country_indicators(self.country, force_refresh=True)
        self.assertIn("NY.GDP.MKTP.CD", indicators)
        self.assertEqual(CountryIndicator.objects.filter(country=self.country).count(), len(COUNTRY_INDICATORS))


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
        # Case A: Identical distributions -> drift score must be 0
        base = ["Diplomacy", "Trade / Economy", "Security / Military"]
        recent = ["Diplomacy", "Trade / Economy", "Security / Military"]
        res_identical = calculate_narrative_shift(base, recent)
        self.assertEqual(res_identical["shift_score"], 0.0)

        # Case B: Completely disjoint distributions -> drift score must be 100
        base_disjoint = ["Diplomacy", "Diplomacy"]
        recent_disjoint = ["Border / Conflict", "Border / Conflict"]
        res_disjoint = calculate_narrative_shift(base_disjoint, recent_disjoint)
        self.assertEqual(res_disjoint["shift_score"], 100.0)
        self.assertEqual(res_disjoint["dominant_later_category"], "Border / Conflict")
        self.assertEqual(res_disjoint["largest_positive_shift"], "Border / Conflict")
        self.assertEqual(res_disjoint["largest_negative_shift"], "Diplomacy")

    def test_narrative_distribution_pandas(self):
        base = ["Diplomacy"] * 8 + ["Trade / Economy"] * 2
        recent = ["Diplomacy"] * 4 + ["Security / Military"] * 6
        res = calculate_narrative_shift(base, recent)
        self.assertGreater(res["shift_score"], 0.0)
        self.assertEqual(res["dominant_later_category"], "Security / Military")


class ScoringServiceTests(TestCase):
    def test_daily_article_counts_group_publication_dates(self):
        now = timezone.now()
        articles = [
            MagicMock(published_at=now - timedelta(days=1, hours=1)),
            MagicMock(published_at=now - timedelta(days=1, hours=2)),
            MagicMock(published_at=now - timedelta(days=3, hours=1)),
        ]

        counts = _daily_article_counts(articles, now)

        self.assertEqual(len(counts), 7)
        self.assertEqual(sum(counts), 3.0)
        self.assertEqual(max(counts), 2.0)

    def test_activity_pulse_statistics(self):
        res = calculate_news_activity([1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(res["average_articles_per_day"], 4.0)
        self.assertEqual(res["peak_day"], 7)
        self.assertEqual(res["peak_article_count"], 7.0)



class ChartGenerationTests(TestCase):
    def test_all_charts_return_valid_png_bytes(self):
        # 1. Timeline chart
        df = pd.DataFrame({
            "date": pd.date_range("2026-09-01", periods=10, freq="D"),
            "volume": [10, 12, 14, 11, 13, 15, 18, 20, 22, 25],
        })
        png_timeline = generate_activity_timeline_chart(df, "India", "China")
        self.assertTrue(png_timeline.startswith(b"\x89PNG"))

        # 2. Narrative chart
        narrative_res = {
            "shift_score": 45.0,
            "categories": [
                {"category": "Diplomacy", "earlier_share": 0.4, "later_share": 0.2},
                {"category": "Security / Military", "earlier_share": 0.2, "later_share": 0.5},
            ],
        }
        png_narrative = generate_narrative_chart(narrative_res, "India", "China")
        self.assertTrue(png_narrative.startswith(b"\x89PNG"))

        # 3. Indicator comparison chart
        ind_a = {"NY.GDP.MKTP.KD.ZG": {"value": 7.2}, "FP.CPI.TOTL.ZG": {"value": 5.4}}
        ind_b = {"NY.GDP.MKTP.KD.ZG": {"value": 5.0}, "FP.CPI.TOTL.ZG": {"value": 2.1}}
        png_ind = generate_indicator_comparison_chart(ind_a, ind_b, "India", "China")
        self.assertTrue(png_ind.startswith(b"\x89PNG"))


class APIStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_countries")

    @patch("intelligence.services.analysis.fetch_all_country_indicators", return_value={})
    @patch("intelligence.services.analysis.get_recent_bilateral_articles")
    def test_gdelt_failure_is_not_reported_as_zero_data(self, mock_articles, mock_indicators):
        mock_articles.side_effect = GDELTError("network unavailable")
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        result = run_country_pair_analysis(india, china, force_refresh=True)
        self.assertFalse(result["metadata"]["geopolitical_data_available"])
        self.assertIsNone(result["news_activity"])
        self.assertIsNone(result["narrative_shift"])

    @patch("intelligence.services.analysis.fetch_all_country_indicators", return_value={})
    @patch("intelligence.services.analysis.get_recent_bilateral_articles")
    def test_gdelt_failure_uses_recent_cached_articles(self, mock_articles, mock_indicators):
        mock_articles.side_effect = GDELTError("network unavailable")
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        Article.objects.create(country_a=india, country_b=china, title="India and China hold cached diplomatic talks", url="https://example.com/cached", source_domain="example.com", published_at=timezone.now() - timedelta(hours=1))
        result = run_country_pair_analysis(india, china, force_refresh=True)
        self.assertEqual(result["metadata"]["gdelt_status"], "success_with_data")
        self.assertEqual(result["metadata"]["source"], "cached")
        self.assertIsNotNone(result["news_activity"])

    @patch("intelligence.services.analysis.fetch_all_country_indicators", return_value={})
    @patch("intelligence.services.analysis.get_recent_bilateral_articles", return_value=[])
    def test_successful_zero_result_is_explicit(self, mock_articles, mock_indicators):
        india = Country.objects.get(iso_code="IND")
        china = Country.objects.get(iso_code="CHN")
        result = run_country_pair_analysis(india, china, force_refresh=True)
        self.assertEqual(result["metadata"]["gdelt_status"], "success_no_data")
        self.assertEqual(result["metadata"]["source"], "live")
        self.assertEqual(result["articles"], [])
        self.assertIsNone(result["narrative_shift"])


class OrchestratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_countries")

    @patch("intelligence.services.analysis.fetch_all_country_indicators")
    @patch("intelligence.services.analysis.get_recent_bilateral_articles")
    def test_run_country_pair_analysis(self, mock_articles, mock_indicators):
        country_a = Country.objects.get(iso_code="IND")
        country_b = Country.objects.get(iso_code="CHN")

        mock_articles.return_value = [
            {
                "title": "Delhi and Beijing agree on diplomatic boundary agreement",
                "url": "https://example.com/news1",
                "source_domain": "example.com",
                "published_at": "2026-10-03T10:00:00Z",
                "tone": 1.2,
            },
            {
                "title": "Troops deploy along Himalayan border region amid tensions",
                "url": "https://example.com/news2",
                "source_domain": "example.com",
                "published_at": "2026-10-04T12:00:00Z",
                "tone": -2.0,
            },
        ]
        mock_indicators.return_value = {
            "NY.GDP.MKTP.CD": {"indicator_code": "NY.GDP.MKTP.CD", "indicator_name": "GDP", "value": 3.9e12, "year": 2025, "country_iso": "IND"}
        }

        output = run_country_pair_analysis(country_a, country_b, force_refresh=True)

        self.assertIn("news_activity", output)
        self.assertIn("narrative_shift", output)
        self.assertIn("indicators", output)
        self.assertEqual(output["countries"]["country_a"]["iso_code"], "IND")
        self.assertEqual(output["countries"]["country_b"]["iso_code"], "CHN")
        mock_articles.assert_called_once_with(country_a, country_b, max_records=250)

        # Verify AnalysisRun was recorded in DB
        self.assertTrue(AnalysisRun.objects.filter(country_a=country_a, country_b=country_b).exists())

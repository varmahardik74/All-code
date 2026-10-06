"""High-level seven-day analysis orchestrator."""
import csv
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from django.conf import settings
import pandas as pd
from intelligence.models import AnalysisRun, Article, Country
from intelligence.services.gdelt import GDELTError, get_recent_bilateral_articles, is_bilateral_relevant, store_articles
from intelligence.services.narrative import calculate_narrative_shift, classify_article
from intelligence.services.pairs import unordered_pair_q
from intelligence.services.scoring import calculate_news_activity
from intelligence.services.worldbank import fetch_all_country_indicators

logger = logging.getLogger(__name__)
ARTICLE_CACHE_MINUTES = 15
ANALYSIS_WINDOW_DAYS = 7
EARLIER_WINDOW_DAYS = 3

def get_country_pair_articles(country_a: Country, country_b: Country):
    return Article.objects.filter(
        unordered_pair_q(country_a, country_b)
    ).order_by("-published_at")


def _seed_csv_urls() -> set[str]:
    seed_path = Path(settings.BASE_DIR) / "between_nations_seed_articles_v1.csv"
    if not seed_path.exists():
        return set()
    with seed_path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            (row.get("url") or "").strip()[:500]
            for row in csv.DictReader(handle)
            if (row.get("url") or "").strip()
        }


def _seeded_pair_articles(country_a: Country, country_b: Country):
    seed_urls = _seed_csv_urls()
    if not seed_urls:
        return []
    return list(
        get_country_pair_articles(country_a, country_b).filter(url__in=seed_urls)
    )

def _window_periods(articles, cutoff):
    earlier_cutoff = cutoff + timedelta(days=EARLIER_WINDOW_DAYS)
    return (
        [a for a in articles if cutoff <= a.published_at < earlier_cutoff],
        [a for a in articles if earlier_cutoff <= a.published_at < cutoff + timedelta(days=ANALYSIS_WINDOW_DAYS)],
    )


def _daily_article_counts(articles, now):
    """Group relevant article publication timestamps into seven calendar days."""
    date_index = pd.date_range(end=now.date(), periods=ANALYSIS_WINDOW_DAYS, freq="D")
    timestamps = pd.to_datetime([article.published_at for article in articles], utc=True)
    if timestamps.empty:
        return [0.0] * ANALYSIS_WINDOW_DAYS
    counts = pd.Series(timestamps.tz_convert(None).normalize()).value_counts()
    return counts.reindex(date_index, fill_value=0).sort_index().astype(float).tolist()

def run_country_pair_analysis(country_a: Country, country_b: Country, force_refresh: bool = False) -> dict:
    """Run the seven-day geopolitical analysis and latest country comparison."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ANALYSIS_WINDOW_DAYS)
    news_data_mode = getattr(settings, "NEWS_DATA_MODE", "live")
    gdelt_status = "unavailable"
    source = news_data_mode if news_data_mode == "seeded" else "live"
    geopolitical_data_available = True
    geopolitical_error = None
    live_urls = None

    if news_data_mode == "seeded":
        seeded_articles = _seeded_pair_articles(country_a, country_b)
        if seeded_articles:
            latest_date = max(article.published_at for article in seeded_articles).date()
            now = datetime.combine(
                latest_date,
                datetime.max.time(),
                tzinfo=timezone.utc,
            )
            cutoff = now - timedelta(days=ANALYSIS_WINDOW_DAYS)
            articles = [
                article
                for article in seeded_articles
                if cutoff <= article.published_at <= now
            ]
            gdelt_status = "success_with_data" if articles else "success_no_data"
        else:
            articles = []
            gdelt_status = "success_no_data"
    else:
        cache_cutoff = now - timedelta(minutes=ARTICLE_CACHE_MINUTES)
        existing = get_country_pair_articles(country_a, country_b)
        has_recent_cache = any(
            a.fetched_at >= cache_cutoff
            and cutoff <= a.published_at < now
            and is_bilateral_relevant(a.title, country_a, country_b)
            for a in existing
        )
        gdelt_status = "success_with_data" if has_recent_cache and not force_refresh else "unavailable"
        source = "cached" if has_recent_cache and not force_refresh else "live"
        if force_refresh or not has_recent_cache:
            try:
                raw_articles = get_recent_bilateral_articles(country_a, country_b, max_records=250)
                store_articles(country_a, country_b, raw_articles, classifier_fn=classify_article)
                live_urls = {item["url"] for item in raw_articles if item.get("url")}
                gdelt_status = "success_with_data" if raw_articles else "success_no_data"
                source = "live"
            except GDELTError as error:
                logger.warning("GDELT query encountered an issue: %s", error)
                cached_articles = [a for a in get_country_pair_articles(country_a, country_b) if cutoff <= a.published_at < now and is_bilateral_relevant(a.title, country_a, country_b)]
                if cached_articles:
                    gdelt_status = "success_with_data"
                    source = "cached"
                else:
                    gdelt_status = "unavailable"
                    geopolitical_data_available = False
                    geopolitical_error = str(error)

        stored_articles = get_country_pair_articles(country_a, country_b)
        if live_urls is not None:
            articles = [a for a in stored_articles if a.url in live_urls and cutoff <= a.published_at < now]
        elif geopolitical_data_available:
            articles = [a for a in stored_articles if cutoff <= a.published_at < now and is_bilateral_relevant(a.title, country_a, country_b)]
        else:
            articles = []

    earlier, later = _window_periods(articles, cutoff)
    activity = None
    narrative = None
    if geopolitical_data_available and articles:
        daily_activity = _daily_article_counts(articles, now)
        activity = calculate_news_activity(daily_activity)
        earlier_categories = [a.category or classify_article(a.title) for a in earlier]
        later_categories = [a.category or classify_article(a.title) for a in later]
        narrative = calculate_narrative_shift(earlier_categories, later_categories) if len(articles) >= 2 else None
    indicators_a = fetch_all_country_indicators(country_a, force_refresh=force_refresh)
    indicators_b = fetch_all_country_indicators(country_b, force_refresh=force_refresh)
    output = {
        "countries": {"country_a": {"name": country_a.name, "iso_code": country_a.iso_code}, "country_b": {"name": country_b.name, "iso_code": country_b.iso_code}},
        "news_activity": activity, "narrative_shift": narrative,
        "articles": [{"id": a.id, "title": a.title, "url": a.url, "source_domain": a.source_domain, "published_at": a.published_at.isoformat(), "tone": a.tone, "category": a.category} for a in articles[:20]],
        "indicators": {country_a.iso_code: indicators_a, country_b.iso_code: indicators_b},
        "metadata": {"fetched_at": now.isoformat(), "analysis_window_days": 7, "analysis_window": "Last 7 Days", "news_data_mode": news_data_mode, "gdelt_status": gdelt_status, "source": source, "geopolitical_data_available": geopolitical_data_available, "geopolitical_error": geopolitical_error, "narrative_status": "available" if narrative else "insufficient_data", "article_count": len(articles), "earlier_article_count": len(earlier), "later_article_count": len(later)},
    }
    AnalysisRun.objects.create(country_a=country_a, country_b=country_b, result_json=output)
    return output

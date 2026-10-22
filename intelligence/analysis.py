"""Local-only analysis pipeline: classification, News Activity, Narrative Shift, orchestration.

Fully local: only reads curated articles (seed-CSV URL allowlist within the fixed
19-day snapshot, 28 Sep - 16 Oct 2026) and locally imported indicators from MySQL.
Narrative Shift measures media coverage emphasis, not real-world geopolitical shifts.
"""

from datetime import datetime, time, timedelta, timezone
import logging
import re
from typing import Sequence

import numpy as np
import pandas as pd

from intelligence.models import AnalysisRun, Article, Country, CountryIndicator, unordered_pair_q
from intelligence.seed import (
    CANONICAL_INDICATORS,
    EARLIER_PERIOD_END,
    LATER_PERIOD_START,
    SNAPSHOT_DAYS,
    SNAPSHOT_END,
    SNAPSHOT_LABEL,
    SNAPSHOT_START,
    load_seed_article_urls,
    snapshot_window_bounds,
)

logger = logging.getLogger(__name__)

WINDOW_START_DT, WINDOW_END_DT = snapshot_window_bounds()
EARLIER_END_DT = datetime.combine(EARLIER_PERIOD_END + timedelta(days=1), time.min, tzinfo=timezone.utc)
LATER_START_DT = datetime.combine(LATER_PERIOD_START, time.min, tzinfo=timezone.utc)

CATEGORIES = [
    "Diplomacy",
    "Security / Military",
    "Trade / Economy",
    "Border / Conflict",
    "Sanctions / Restrictions",
    "Technology / Strategy",
]

CATEGORY_KEYWORDS = {
    "Diplomacy": [
        r"\btalks?\b", r"\bmeeting\b", r"\bsummit\b", r"\bdialogue\b", r"\bagreement\b",
        r"\bnegotiat\w+", r"\bdiplomat\w*", r"\bambassador\b", r"\bforeign minister\b",
        r"\btreaty\b", r"\benvoy\b", r"\bbilateral\b", r"\bpeace\b", r"\baccord\b",
        r"\bcommunique\b", r"\bcooperat\w+",
    ],
    "Security / Military": [
        r"\bmilitary\b", r"\btroops?\b", r"\barmy\b", r"\bnavy\b", r"\bnavies\b",
        r"\bmissiles?\b", r"\bdefense\b", r"\bdefence\b", r"\bweapons?\b", r"\bdrills?\b",
        r"\bairstrike\w*", r"\bair\s*force\b", r"\bwarships?\b", r"\bsoldiers?\b",
        r"\bnuclear\b", r"\bammunition\b", r"\bexercise\w*", r"\barmaments?\b",
        r"\bdrone strikes?\b",
    ],
    "Trade / Economy": [
        r"\btrade\b", r"\btariffs?\b", r"\bexports?\b", r"\bimports?\b", r"\binvestment\b",
        r"\beconomy\b", r"\beconomic\b", r"\bbusiness\b", r"\bcommerce\b", r"\bmarket\b",
        r"\bcurrency\b", r"\bsupply chain\b", r"\bdebt\b", r"\bfinancial\b", r"\bgdp\b",
        r"\bfta\b", r"\bcustoms\b",
    ],
    "Border / Conflict": [
        r"\bborder\b", r"\bclash\w*", r"\bconflict\b", r"\battack\w*", r"\bfighting\b",
        r"\bceasefire\b", r"\bdisputed\b", r"\bstandoff\b", r"\bskirmish\w*", r"\bincursion\b",
        r"\bhostilit\w+", r"\bfrontline\b", r"\bcasualt\w+", r"\btension\w*", r"\bwar\b",
        r"\binvasion\b",
    ],
    "Sanctions / Restrictions": [
        r"\bsanctions?\b", r"\bembargo\w*", r"\brestrictions?\b", r"\bban\b", r"\bbans\b",
        r"\bbanned\b", r"\bblacklist\w*", r"\bpenalt\w+", r"\bexport controls?\b", r"\bcurbs?\b",
        r"\bboycott\b", r"\basset freeze\b", r"\bseizure\b",
    ],
    "Technology / Strategy": [
        r"\bchips?\b", r"\bsemiconductor\w*", r"\btechnolog\w+", r"\btech\b", r"\bai\b",
        r"\bartificial intelligence\b", r"\bstrategic\b", r"\bcyber\w*", r"\bsatellite\w*",
        r"\bspace\b", r"\btelecom\w*", r"\bquantum\b", r"\bcritical minerals?\b",
        r"\bsurveillance\b", r"\b5g\b",
    ],
}

_COMPILED_PATTERNS = {
    category: [re.compile(kw, re.IGNORECASE) for kw in keywords]
    for category, keywords in CATEGORY_KEYWORDS.items()
}


def classify_article(title: str) -> str:
    """Deterministic keyword classification; most keyword hits wins, ties default to Diplomacy."""
    if not title or not isinstance(title, str):
        return "Diplomacy"
    scores = {}
    for category, patterns in _COMPILED_PATTERNS.items():
        count = sum(1 for pattern in patterns if pattern.search(title))
        if count > 0:
            scores[category] = count
    if not scores:
        return "Diplomacy"
    return max(CATEGORIES, key=lambda c: scores.get(c, 0))


def calculate_news_activity(daily_volumes: Sequence[float]) -> dict:
    """Summarize daily counts across the 19-day window (zero days stay in the average)."""
    values = np.asarray(list(daily_volumes), dtype=float)
    if values.size == 0:
        values = np.zeros(SNAPSHOT_DAYS, dtype=float)
    peak_index = int(np.argmax(values))
    return {
        "daily_article_count": values.tolist(),
        "average_articles_per_day": round(float(np.mean(values)), 2),
        "peak_day": peak_index + 1,
        "peak_article_count": float(values[peak_index]),
    }


def get_country_pair_articles(country_a: Country, country_b: Country):
    return Article.objects.filter(unordered_pair_q(country_a, country_b)).order_by("-published_at")


def _curated_pair_articles(country_a: Country, country_b: Country):
    """Only rows whose URL is in the seed CSV allowlist and inside the snapshot window."""
    seed_urls = load_seed_article_urls()
    if not seed_urls:
        return []
    return list(
        get_country_pair_articles(country_a, country_b)
        .filter(url__in=seed_urls)
        .filter(published_at__gte=WINDOW_START_DT, published_at__lt=WINDOW_END_DT)
    )


def _window_periods(articles):
    return (
        [a for a in articles if WINDOW_START_DT <= a.published_at < EARLIER_END_DT],
        [a for a in articles if LATER_START_DT <= a.published_at < WINDOW_END_DT],
    )


def _daily_article_counts(articles):
    date_index = pd.date_range(start=SNAPSHOT_START, end=SNAPSHOT_END, freq="D")
    timestamps = pd.to_datetime([article.published_at for article in articles], utc=True)
    if timestamps.empty:
        return [0.0] * SNAPSHOT_DAYS
    counts = pd.Series(timestamps.tz_convert(None).normalize()).value_counts()
    return counts.reindex(date_index, fill_value=0).sort_index().astype(float).tolist()


def calculate_narrative_shift(
    earlier_categories: Sequence[str],
    later_categories: Sequence[str],
) -> dict | None:
    """Total Variation Distance of category shares, 0-100; None when either period is empty."""
    earlier_categories = list(earlier_categories)
    later_categories = list(later_categories)
    if not earlier_categories or not later_categories:
        return None

    earlier_series = pd.Series(earlier_categories, dtype=object)
    later_series = pd.Series(later_categories, dtype=object)
    e_counts = earlier_series.value_counts()
    l_counts = later_series.value_counts()
    e_total = len(earlier_series)
    l_total = len(later_series)

    rows = []
    total_variation = 0.0
    for cat in CATEGORIES:
        e_count = int(e_counts.get(cat, 0))
        l_count = int(l_counts.get(cat, 0))
        e_share = e_count / e_total
        l_share = l_count / l_total
        diff = l_share - e_share
        total_variation += abs(diff)
        rows.append({
            "category": cat,
            "earlier_count": e_count,
            "later_count": l_count,
            "earlier_share": round(e_share, 4),
            "later_share": round(l_share, 4),
            "difference": round(diff, 4),
        })

    shift_score = round(min(100.0, max(0.0, (total_variation / 2.0) * 100.0)), 2)
    df_rows = pd.DataFrame(rows)
    dominant_later = str(df_rows.sort_values("later_share", ascending=False).iloc[0]["category"])
    dominant_earlier = str(df_rows.sort_values("earlier_share", ascending=False).iloc[0]["category"])
    largest_positive = str(df_rows.sort_values("difference", ascending=False).iloc[0]["category"])
    largest_negative = str(df_rows.sort_values("difference", ascending=True).iloc[0]["category"])

    if shift_score >= 40.0:
        interpretation = (
            f"Media coverage shifted strongly toward {largest_positive} "
            f"during the latter part of the snapshot period."
        )
    elif shift_score >= 20.0:
        interpretation = (
            f"Moderate change in reporting emphasis toward {largest_positive} "
            f"during the latter part of the snapshot period."
        )
    else:
        interpretation = "Coverage emphasis remained largely stable across the snapshot window."

    return {
        "categories": rows,
        "shift_score": shift_score,
        "dominant_later_category": dominant_later,
        "dominant_earlier_category": dominant_earlier,
        "largest_positive_shift": largest_positive,
        "largest_negative_shift": largest_negative,
        "interpretation": interpretation,
        "earlier_article_count": e_total,
        "later_article_count": l_total,
    }


def _country_indicators(country: Country) -> dict:
    """Latest local observation per canonical code; missing codes keep None (N/A, never zero)."""
    latest_by_code = {}
    for row in CountryIndicator.objects.filter(country=country).order_by("indicator_code", "-year"):
        latest_by_code.setdefault(row.indicator_code, row)
    result = {}
    for code, name in CANONICAL_INDICATORS.items():
        row = latest_by_code.get(code)
        result[code] = {
            "indicator_code": code,
            "indicator_name": name,
            "value": row.value if row else None,
            "year": row.year if row else None,
            "country_iso": country.iso_code,
        }
    return result


def run_country_pair_analysis(country_a: Country, country_b: Country) -> dict:
    """Compose the full local analysis for a pair and persist an AnalysisRun row."""
    now = datetime.now(timezone.utc)
    articles = _curated_pair_articles(country_a, country_b)
    data_status = "success_with_data" if articles else "success_no_data"

    earlier, later = _window_periods(articles)
    activity = None
    narrative = None
    if articles:
        activity = calculate_news_activity(_daily_article_counts(articles))
        earlier_categories = [a.category or classify_article(a.title) for a in earlier]
        later_categories = [a.category or classify_article(a.title) for a in later]
        narrative = calculate_narrative_shift(earlier_categories, later_categories) if earlier and later else None

    output = {
        "countries": {
            "country_a": {"name": country_a.name, "iso_code": country_a.iso_code},
            "country_b": {"name": country_b.name, "iso_code": country_b.iso_code},
        },
        "news_activity": activity,
        "narrative_shift": narrative,
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "url": a.url,
                "source_domain": a.source_domain,
                "published_at": a.published_at.isoformat(),
                "tone": a.tone,
                "category": a.category,
            }
            for a in articles[:20]
        ],
        "indicators": {
            country_a.iso_code: _country_indicators(country_a),
            country_b.iso_code: _country_indicators(country_b),
        },
        "metadata": {
            "fetched_at": now.isoformat(),
            "analysis_window_days": SNAPSHOT_DAYS,
            "analysis_window": SNAPSHOT_LABEL,
            "window_start": SNAPSHOT_START.isoformat(),
            "window_end": SNAPSHOT_END.isoformat(),
            "source": "local",
            "data_status": data_status,
            "narrative_status": "available" if narrative is not None else "insufficient_data",
            "article_count": len(articles),
            "earlier_article_count": len(earlier),
            "later_article_count": len(later),
        },
    }
    AnalysisRun.objects.create(country_a=country_a, country_b=country_b, result_json=output)
    return output

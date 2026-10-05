"""Geopolitical article classification and Narrative Shift analysis.

This module provides:
1. Rule-based deterministic keyword classification of news article titles
   into one of six core geopolitical categories.
2. 7-Day Narrative Shift: compares category distribution between the
   FIRST 3 DAYS and LAST 4 DAYS of the same seven-day analysis window.

ANALYSIS WINDOW:
- Earlier period: first 3 days of the 7-day window
- Later period:   last 4 days of the 7-day window

IMPORTANT INTERPRETATION NOTE:
Narrative Shift measures shifts in media coverage emphasis within the
seven-day window. It does NOT assert or prove actual geopolitical shifts
on the ground. Wording must remain: "media narrative shift" or
"change in reporting emphasis during the latter part of the period."
"""

import re
from typing import Sequence

import pandas as pd

# The 6 core geopolitical categories
CATEGORIES = [
    "Diplomacy",
    "Security / Military",
    "Trade / Economy",
    "Border / Conflict",
    "Sanctions / Restrictions",
    "Technology / Strategy",
]

# Curated, deterministic keyword lists for each category
CATEGORY_KEYWORDS = {
    "Diplomacy": [
        r"\btalks?\b",
        r"\bmeeting\b",
        r"\bsummit\b",
        r"\bdialogue\b",
        r"\bagreement\b",
        r"\bnegotiat\w+",
        r"\bdiplomat\w*",
        r"\bambassador\b",
        r"\bforeign minister\b",
        r"\btreaty\b",
        r"\benvoy\b",
        r"\bbilateral\b",
        r"\bpeace\b",
        r"\baccord\b",
        r"\bcommunique\b",
        r"\bcooperat\w+",
    ],
    "Security / Military": [
        r"\bmilitary\b",
        r"\btroops?\b",
        r"\barmy\b",
        r"\bnavy\b",
        r"\bnavies\b",
        r"\bmissiles?\b",
        r"\bdefense\b",
        r"\bdefence\b",
        r"\bweapons?\b",
        r"\bdrills?\b",
        r"\bairstrike\w*",
        r"\bair\s*force\b",
        r"\bwarships?\b",
        r"\bsoldiers?\b",
        r"\bnuclear\b",
        r"\bammunition\b",
        r"\bexercise\w*",
        r"\barmaments?\b",
        r"\bdrone strikes?\b",
    ],
    "Trade / Economy": [
        r"\btrade\b",
        r"\btariffs?\b",
        r"\bexports?\b",
        r"\bimports?\b",
        r"\binvestment\b",
        r"\beconomy\b",
        r"\beconomic\b",
        r"\bbusiness\b",
        r"\bcommerce\b",
        r"\bmarket\b",
        r"\bcurrency\b",
        r"\bsupply chain\b",
        r"\bdebt\b",
        r"\bfinancial\b",
        r"\bgdp\b",
        r"\bfta\b",
        r"\bcustoms\b",
    ],
    "Border / Conflict": [
        r"\bborder\b",
        r"\bclash\w*",
        r"\bconflict\b",
        r"\battack\w*",
        r"\bfighting\b",
        r"\bceasefire\b",
        r"\bdisputed\b",
        r"\bstandoff\b",
        r"\bskirmish\w*",
        r"\bincursion\b",
        r"\bhostilit\w+",
        r"\bfrontline\b",
        r"\bcasualt\w+",
        r"\btension\w*",
        r"\bwar\b",
        r"\binvasion\b",
    ],
    "Sanctions / Restrictions": [
        r"\bsanctions?\b",
        r"\bembargo\w*",
        r"\brestrictions?\b",
        r"\bban\b",
        r"\bbans\b",
        r"\bbanned\b",
        r"\bblacklist\w*",
        r"\bpenalt\w+",
        r"\bexport controls?\b",
        r"\bcurbs?\b",
        r"\bboycott\b",
        r"\basset freeze\b",
        r"\bseizure\b",
    ],
    "Technology / Strategy": [
        r"\bchips?\b",
        r"\bsemiconductor\w*",
        r"\btechnolog\w+",
        r"\btech\b",
        r"\bai\b",
        r"\bartificial intelligence\b",
        r"\bstrategic\b",
        r"\bcyber\w*",
        r"\bsatellite\w*",
        r"\bspace\b",
        r"\btelecom\w*",
        r"\bquantum\b",
        r"\bcritical minerals?\b",
        r"\bsurveillance\b",
        r"\b5g\b",
    ],
}

_COMPILED_PATTERNS = {
    category: [re.compile(kw, re.IGNORECASE) for kw in keywords]
    for category, keywords in CATEGORY_KEYWORDS.items()
}


def classify_article(title: str) -> str:
    """Classify a news article title into one of the 6 geopolitical categories.

    Uses deterministic regular-expression keyword matching. The category with
    the highest keyword-hit count wins. Ties or zero-matches default to Diplomacy.
    """
    if not title or not isinstance(title, str):
        return "Diplomacy"

    scores = {}
    for category, patterns in _COMPILED_PATTERNS.items():
        count = sum(1 for pattern in patterns if pattern.search(title))
        if count > 0:
            scores[category] = count

    if not scores:
        return "Diplomacy"

    best_category = max(CATEGORIES, key=lambda c: scores.get(c, 0))
    return best_category


def calculate_narrative_shift(
    earlier_categories: Sequence[str],
    later_categories: Sequence[str],
) -> dict:
    """Calculate 7-Day Narrative Shift between the earlier 3-day and later 4-day periods.

    Narrative Shift formula:
        Total Variation Distance (TVD) = 0.5 * sum(|later_share - earlier_share|)
        Narrative Shift Score = TVD * 100 (bounded 0–100)

    Parameters:
    - earlier_categories: category labels for articles in the first 3 days.
    - later_categories:   category labels for articles in the last 4 days.

    Returns dict containing:
    - categories: list of per-category dicts with shares and difference
    - shift_score: float (0.0–100.0)
    - dominant_later_category: str
    - largest_positive_shift: str
    - largest_negative_shift: str
    - interpretation: str
    - earlier_article_count: int
    - later_article_count: int
    """
    earlier_series = pd.Series(list(earlier_categories), dtype=object)
    later_series = pd.Series(list(later_categories), dtype=object)

    e_counts = earlier_series.value_counts()
    l_counts = later_series.value_counts()

    e_total = len(earlier_series)
    l_total = len(later_series)

    rows = []
    total_variation = 0.0

    for cat in CATEGORIES:
        e_count = int(e_counts.get(cat, 0))
        l_count = int(l_counts.get(cat, 0))

        e_share = (e_count / e_total) if e_total > 0 else (1.0 / len(CATEGORIES))
        l_share = (l_count / l_total) if l_total > 0 else (1.0 / len(CATEGORIES))

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
            f"during the latter part of the seven-day period."
        )
    elif shift_score >= 20.0:
        interpretation = (
            f"Moderate change in reporting emphasis toward {largest_positive} "
            f"during the latter part of the seven-day period."
        )
    else:
        interpretation = "Coverage emphasis remained largely stable across the seven-day window."

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

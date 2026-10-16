"""Shared dataset helpers for the local historical snapshot.

This module is the single source of truth for:

- The fixed 19-day historical window (28 September - 16 October 2026,
  inclusive). The dashboard analyses this fixed snapshot; it is not a
  rolling window.
- The article CSV URL allowlist used by both the importer and the analysis,
  so non-curated database rows never contaminate the analysis.
- The local country-indicator CSV exported from MySQL.

The application is fully local: curated articles and country indicators are
loaded from these CSV files into MySQL, and the analysis never contacts an
external API.
"""

import csv
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from django.conf import settings

# Fixed historical snapshot window (inclusive dates).
SNAPSHOT_START = date(2026, 9, 28)
SNAPSHOT_END = date(2026, 10, 16)
SNAPSHOT_DAYS = (SNAPSHOT_END - SNAPSHOT_START).days + 1  # 19
SNAPSHOT_LABEL = "Historical snapshot: 28 September–16 October 2026 (19 days)"

# Narrative Shift periods inside the snapshot.
# Earlier: 28 September - 6 October (9 days). Later: 7 - 16 October (10 days).
EARLIER_PERIOD_END = date(2026, 10, 6)
LATER_PERIOD_START = date(2026, 10, 7)

# Data files at the project root.
SEED_CSV_FILENAME = "seed_articles.csv"
INDICATORS_CSV_FILENAME = "country_indicators.csv"
DEFAULT_SEED_CSV_PATH = settings.BASE_DIR / SEED_CSV_FILENAME
DEFAULT_INDICATORS_CSV_PATH = settings.BASE_DIR / INDICATORS_CSV_FILENAME

# The 10 curated World Bank indicator codes captured in the local snapshot.
# The values are exported database records, never live API results.
CANONICAL_INDICATORS = {
    "NY.GDP.MKTP.CD": "GDP (current US$)",
    "NY.GDP.MKTP.KD.ZG": "GDP Growth Rate (annual %)",
    "NY.GDP.PCAP.CD": "GDP per Capita (current US$)",
    "SP.POP.TOTL": "Population, total",
    "SP.POP.GROW": "Population Growth Rate (annual %)",
    "FP.CPI.TOTL.ZG": "Inflation Rate (consumer prices, annual %)",
    "SL.UEM.TOTL.ZS": "Unemployment Rate (% of total labor force)",
    "NE.TRD.GNFS.ZS": "Trade (% of GDP)",
    "MS.MIL.XPND.GD.ZS": "Military Expenditure (% of GDP)",
    "NE.EXP.GNFS.ZS": "Exports of Goods and Services (% of GDP)",
}


def load_seed_article_urls(path: str | Path | None = None) -> set[str]:
    """Return the set of article URLs in the seed article CSV.

    The dataset is a UTF-8 CSV with one verified article per row. Files are
    read with ``utf-8-sig`` so an optional byte-order mark is tolerated.
    """
    seed_path = Path(path) if path is not None else DEFAULT_SEED_CSV_PATH
    if not seed_path.exists():
        return set()
    with seed_path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            (row.get("url") or "").strip()[:500]
            for row in csv.DictReader(handle)
            if (row.get("url") or "").strip()
        }


def load_indicator_rows(path: str | Path | None = None) -> list[dict]:
    """Return the rows of the local country-indicator CSV.

    Missing values remain missing: rows without a usable ``value`` are kept
    with a ``None`` value so importers can skip or report them explicitly.
    """
    indicator_path = Path(path) if path is not None else DEFAULT_INDICATORS_CSV_PATH
    if not indicator_path.exists():
        return []
    with indicator_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = []
        for row in csv.DictReader(handle):
            if not (row.get("iso_code") or "").strip():
                continue
            value_raw = (row.get("value") or "").strip()
            try:
                value = float(value_raw) if value_raw != "" else None
            except (TypeError, ValueError):
                value = None
            year_raw = (row.get("year") or "").strip()
            rows.append({
                "iso_code": (row.get("iso_code") or "").strip().upper(),
                "country_name": (row.get("country_name") or "").strip(),
                "indicator_code": (row.get("indicator_code") or "").strip(),
                "indicator_name": (row.get("indicator_name") or "").strip(),
                "value": value,
                "year": int(year_raw) if year_raw.isdigit() else None,
                "fetched_at": (row.get("fetched_at") or "").strip(),
                "source": (row.get("source") or "").strip(),
            })
        return rows


def snapshot_window_bounds():
    """Return the half-open (start, end) datetime window for the snapshot."""
    start = _datetime_start(SNAPSHOT_START)
    end = _datetime_start(SNAPSHOT_END + timedelta(days=1))
    return start, end


def _datetime_start(day: date):
    """Return the UTC midnight datetime for a date."""
    return datetime.combine(day, time.min, tzinfo=timezone.utc)
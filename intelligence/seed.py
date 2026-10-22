"""Fixed 19-day snapshot constants, CSV loaders, and the idempotent MySQL importer.

Run standalone with: python -m intelligence.seed [articles.csv] [indicators.csv]
"""

import csv
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from django.apps import apps as _django_apps

if not _django_apps.ready:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()

from django.conf import settings

from intelligence.models import Article, Country, CountryIndicator, unordered_pair_q

SNAPSHOT_START = date(2026, 9, 28)
SNAPSHOT_END = date(2026, 10, 16)
SNAPSHOT_DAYS = (SNAPSHOT_END - SNAPSHOT_START).days + 1
SNAPSHOT_LABEL = "Historical snapshot: 28 September–16 October 2026 (19 days)"
EARLIER_PERIOD_END = date(2026, 10, 6)
LATER_PERIOD_START = date(2026, 10, 7)

SEED_CSV_FILENAME = "seed_articles.csv"
INDICATORS_CSV_FILENAME = "country_indicators.csv"
DEFAULT_SEED_CSV_PATH = settings.BASE_DIR / SEED_CSV_FILENAME
DEFAULT_INDICATORS_CSV_PATH = settings.BASE_DIR / INDICATORS_CSV_FILENAME

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

COUNTRIES = [
    ("China", "CHN"),
    ("India", "IND"),
    ("Iran", "IRN"),
    ("Russia", "RUS"),
    ("Germany", "DEU"),
    ("Japan", "JPN"),
    ("United Kingdom", "GBR"),
    ("United States", "USA"),
    ("Pakistan", "PAK"),
    ("Israel", "ISR"),
]
ACTIVE_COUNTRY_CODES = {iso_code for _, iso_code in COUNTRIES}

TONE_VALUES = {"negative": -1.0, "neutral": 0.0, "positive": 1.0, "": None}

CATEGORY_VALUES = {
    "Diplomacy": "Diplomacy",
    "Politics": "Diplomacy",
    "Security/Military": "Security / Military",
    "Security / Military": "Security / Military",
    "Trade/Economy": "Trade / Economy",
    "Trade / Economy": "Trade / Economy",
    "Technology": "Technology / Strategy",
    "Technology / Strategy": "Technology / Strategy",
    "Border / Conflict": "Border / Conflict",
    "Sanctions / Restrictions": "Sanctions / Restrictions",
}


class DatasetError(Exception):
    pass


def load_seed_article_urls(path=None) -> set[str]:
    seed_path = Path(path) if path is not None else DEFAULT_SEED_CSV_PATH
    if not seed_path.exists():
        return set()
    with seed_path.open(newline="", encoding="utf-8-sig") as handle:
        return {
            (row.get("url") or "").strip()[:500]
            for row in csv.DictReader(handle)
            if (row.get("url") or "").strip()
        }


def load_indicator_rows(path=None) -> list[dict]:
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
    start = datetime.combine(SNAPSHOT_START, time.min, tzinfo=timezone.utc)
    end = datetime.combine(SNAPSHOT_END + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def canonical_pair_key(iso_a: str, iso_b: str) -> str:
    codes = sorted([iso_a.upper().strip(), iso_b.upper().strip()])
    return f"{codes[0]}_{codes[1]}"


def order_countries_by_iso(country_a, country_b):
    return tuple(sorted([country_a, country_b], key=lambda country: country.iso_code))


def _seed_countries(out):
    created = updated = 0
    Country.objects.exclude(iso_code__in=ACTIVE_COUNTRY_CODES).update(is_active=False)
    for name, iso_code in COUNTRIES:
        _, was_created = Country.objects.update_or_create(
            iso_code=iso_code, defaults={"name": name, "is_active": True}
        )
        created += was_created
        updated += not was_created
    print(f"Seeded {len(COUNTRIES)} countries ({created} created, {updated} updated).", file=out)


def _validate_article_row(row, line_number, countries):
    iso_a = (row.get("country_a_iso") or "").strip().upper()
    iso_b = (row.get("country_b_iso") or "").strip().upper()
    title = (row.get("title") or "").strip()
    url = (row.get("url") or "").strip()
    if iso_a not in ACTIVE_COUNTRY_CODES or iso_b not in ACTIVE_COUNTRY_CODES:
        raise ValueError(f"Line {line_number}: country codes must be active 10-country ISO codes.")
    if iso_a == iso_b:
        raise ValueError(f"Line {line_number}: country codes must differ.")
    if not url or not title:
        raise ValueError(f"Line {line_number}: url and title are required.")
    published_at = _parse_publication_timestamp(row, line_number)
    if not SNAPSHOT_START <= published_at.date() <= SNAPSHOT_END:
        raise ValueError(
            f"Line {line_number}: date must fall within snapshot ({SNAPSHOT_START} to {SNAPSHOT_END})."
        )
    source_domain = (row.get("source_domain") or "").strip() or urlparse(url).netloc
    if not source_domain:
        raise ValueError(f"Line {line_number}: source_domain is required.")
    return {
        "country_a": countries[iso_a],
        "country_b": countries[iso_b],
        "title": title[:500],
        "url": url[:500],
        "source_domain": source_domain[:255],
        "published_at": published_at,
        "tone": TONE_VALUES.get((row.get("tone") or "").strip().lower()),
        "category": CATEGORY_VALUES.get((row.get("category") or "").strip(), "Diplomacy"),
    }


def _parse_publication_timestamp(row, line_number):
    exact = (row.get("published_at_utc") or "").strip()
    if exact:
        try:
            parsed = datetime.fromisoformat(exact.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Line {line_number}: invalid published_at_utc {exact!r}.") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    published_date = (row.get("published_date") or "").strip()
    try:
        return datetime.combine(date.fromisoformat(published_date), time.min, tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Line {line_number}: invalid published_date {published_date!r}.") from exc


def _seed_articles(csv_path, out):
    if not csv_path.exists():
        raise DatasetError(f"Seed article CSV not found: {csv_path}")
    countries = {
        country.iso_code: country
        for country in Country.objects.filter(iso_code__in=ACTIVE_COUNTRY_CODES, is_active=True)
    }
    missing = sorted(ACTIVE_COUNTRY_CODES - set(countries))
    if missing:
        raise DatasetError("Missing active countries: " + ", ".join(missing))

    rows_read = inserted = duplicates = rejected = 0
    unique_pairs = set()
    errors = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            rows_read += 1
            try:
                data = _validate_article_row(row, line_number, countries)
            except ValueError as exc:
                rejected += 1
                errors.append(str(exc))
                continue
            country_a, country_b = order_countries_by_iso(data["country_a"], data["country_b"])
            unique_pairs.add(canonical_pair_key(country_a.iso_code, country_b.iso_code))
            if Article.objects.filter(url=data["url"]).filter(unordered_pair_q(country_a, country_b)).exists():
                duplicates += 1
                continue
            Article.objects.create(**data)
            inserted += 1

    total_seeded = Article.objects.filter(url__in=load_seed_article_urls(csv_path)).count()
    print(f"Rows read: {rows_read}", file=out)
    print(f"Rows inserted: {inserted}", file=out)
    print(f"Rows skipped as duplicates: {duplicates}", file=out)
    print(f"Rows rejected: {rejected}", file=out)
    print(f"Unique pairs represented: {len(unique_pairs)}", file=out)
    print(f"Total seeded articles: {total_seeded}", file=out)
    if errors:
        print("Article validation errors:", file=out)
        for error in errors:
            print(f"  - {error}", file=out)
    print("Seed article import complete.", file=out)
    if rejected:
        raise DatasetError(f"{rejected} seed article row(s) failed validation.")


def _prune_stale_indicators(active_countries, covered_with_value):
    active_ids = [country.pk for country in active_countries.values()]
    pruned = 0
    for row in CountryIndicator.objects.filter(country_id__in=active_ids).select_related("country").iterator():
        if (row.country.iso_code, row.indicator_code) not in covered_with_value:
            row.delete()
            pruned += 1
    return pruned


def _seed_indicators(csv_path, out):
    if not csv_path.exists():
        raise DatasetError(f"Country indicator CSV not found: {csv_path}")
    countries = {
        country.iso_code: country
        for country in Country.objects.filter(iso_code__in=ACTIVE_COUNTRY_CODES, is_active=True)
    }
    rows_read = inserted = updated = skipped = rejected = 0
    errors = []
    covered = set()
    for row in load_indicator_rows(csv_path):
        rows_read += 1
        iso, code = row["iso_code"], row["indicator_code"]
        if not code or iso not in countries:
            rejected += 1
            errors.append(f"Row {rows_read}: invalid country/indicator ({iso} / {code!r}).")
            continue
        if row["value"] is None or row["year"] is None:
            skipped += 1
            continue
        covered.add((iso, code))
        _, was_created = CountryIndicator.objects.update_or_create(
            country=countries[iso],
            indicator_code=code,
            year=row["year"],
            defaults={"indicator_name": row["indicator_name"] or code, "value": row["value"]},
        )
        inserted += was_created
        updated += not was_created

    pruned = _prune_stale_indicators(countries, covered)
    if errors:
        print("Indicator validation errors:", file=out)
        for error in errors:
            print(f"  - {error}", file=out)
    print(f"Indicator rows read: {rows_read}", file=out)
    print(f"Indicator rows inserted: {inserted}", file=out)
    print(f"Indicator rows updated: {updated}", file=out)
    print(f"Indicator rows skipped (no value/year): {skipped}", file=out)
    print(f"Indicator rows rejected: {rejected}", file=out)
    print(f"Stale indicator rows pruned: {pruned}", file=out)
    print("Indicator import complete.", file=out)


def import_dataset(articles=None, indicators=None, out=None):
    if out is None:
        out = sys.stdout
    _seed_countries(out)
    _seed_articles(Path(articles) if articles else DEFAULT_SEED_CSV_PATH, out)
    _seed_indicators(Path(indicators) if indicators else DEFAULT_INDICATORS_CSV_PATH, out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        import_dataset(
            articles=argv[0] if len(argv) > 0 else None,
            indicators=argv[1] if len(argv) > 1 else None,
        )
    except DatasetError as exc:
        print(f"Dataset import failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

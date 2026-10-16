"""Seed the fixed local historical dataset.

Sets up the 10-country universe, imports the curated historical articles, and
imports the locally exported country indicators in one idempotent command.

Articles are validated and imported from ``seed_articles.csv``. Indicators are
read from ``country_indicators.csv`` (the locally exported MySQL snapshot) and
stored with ``update_or_create`` so repeated runs never create duplicates and
never delete existing rows. Genuinely missing values are left missing.
"""

import csv
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError

from intelligence.models import Article, Country, CountryIndicator
from intelligence.pairs import canonical_pair_key, order_countries_by_iso, unordered_pair_q
from intelligence.seeds import (
    DEFAULT_INDICATORS_CSV_PATH,
    DEFAULT_SEED_CSV_PATH,
    SEED_CSV_FILENAME,
    SNAPSHOT_END,
    SNAPSHOT_START,
    load_indicator_rows,
    load_seed_article_urls,
)

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

TONE_VALUES = {
    "negative": -1.0,
    "neutral": 0.0,
    "positive": 1.0,
    "": None,
}

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


class Command(BaseCommand):
    help = f"Seed the local dataset: countries, curated articles from {SEED_CSV_FILENAME}, and locally exported country indicators."

    def add_arguments(self, parser):
        parser.add_argument(
            "--articles",
            type=str,
            default=str(DEFAULT_SEED_CSV_PATH),
            help="Path to the seed article CSV.",
        )
        parser.add_argument(
            "--indicators",
            type=str,
            default=str(DEFAULT_INDICATORS_CSV_PATH),
            help="Path to the locally exported country-indicator CSV.",
        )

    def handle(self, *args, **options):
        articles_path = Path(options["articles"])
        indicators_path = Path(options["indicators"])

        self._seed_countries()

        article_stats = self._seed_articles(articles_path)
        for line, count in article_stats["counts"].items():
            self.stdout.write(f"{line}: {count}")
        self.stdout.write(f"Unique pairs represented: {article_stats['unique_pairs']}")
        self.stdout.write(f"Total seeded articles: {article_stats['total_seeded']}")
        if article_stats["errors"]:
            self.stdout.write(self.style.WARNING("Article validation errors:"))
            for error in article_stats["errors"]:
                self.stdout.write(f"  - {error}")
        self.stdout.write(self.style.SUCCESS("Seed article import complete."))
        if article_stats["rejected"]:
            raise CommandError(
                f"{article_stats['rejected']} seed article row(s) failed validation."
            )

        indicator_stats = self._seed_indicators(indicators_path)
        for line, count in indicator_stats.items():
            self.stdout.write(f"{line}: {count}")
        self.stdout.write(self.style.SUCCESS("Indicator import complete."))

    # ------------------------------------------------------------------
    # Countries
    # ------------------------------------------------------------------

    def _seed_countries(self):
        created_count = 0
        updated_count = 0
        Country.objects.exclude(iso_code__in=ACTIVE_COUNTRY_CODES).update(is_active=False)
        for name, iso_code in COUNTRIES:
            _, created = Country.objects.update_or_create(
                iso_code=iso_code,
                defaults={"name": name, "is_active": True},
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(COUNTRIES)} countries "
                f"({created_count} created, {updated_count} updated)."
            )
        )

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------

    def _seed_articles(self, csv_path):
        if not csv_path.exists():
            raise CommandError(f"Seed article CSV not found: {csv_path}")

        countries = {
            country.iso_code: country
            for country in Country.objects.filter(
                iso_code__in=ACTIVE_COUNTRY_CODES, is_active=True
            )
        }
        missing = sorted(ACTIVE_COUNTRY_CODES - set(countries))
        if missing:
            raise CommandError(
                "Missing active countries required by seed CSV: " + ", ".join(missing)
            )

        rows_read = 0
        inserted = 0
        duplicates = 0
        rejected = 0
        unique_pairs = set()
        errors = []

        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for line_number, row in enumerate(reader, start=2):
                rows_read += 1
                try:
                    article_data = self._validate_article_row(row, line_number, countries)
                except ValueError as exc:
                    rejected += 1
                    errors.append(str(exc))
                    continue

                country_a, country_b = order_countries_by_iso(
                    article_data["country_a"], article_data["country_b"]
                )
                unique_pairs.add(
                    canonical_pair_key(country_a.iso_code, country_b.iso_code)
                )

                exists = Article.objects.filter(url=article_data["url"]).filter(
                    unordered_pair_q(country_a, country_b)
                ).exists()
                if exists:
                    duplicates += 1
                    continue

                Article.objects.create(
                    country_a=country_a,
                    country_b=country_b,
                    title=article_data["title"],
                    url=article_data["url"],
                    source_domain=article_data["source_domain"],
                    published_at=article_data["published_at"],
                    tone=article_data["tone"],
                    category=article_data["category"],
                )
                inserted += 1

        total_seeded = Article.objects.filter(
            url__in=load_seed_article_urls(csv_path),
        ).count()
        return {
            "counts": {
                "Rows read": rows_read,
                "Rows inserted": inserted,
                "Rows skipped as duplicates": duplicates,
                "Rows rejected": rejected,
            },
            "unique_pairs": len(unique_pairs),
            "total_seeded": total_seeded,
            "errors": errors,
            "rejected": rejected,
        }

    def _validate_article_row(self, row, line_number, countries):
        iso_a = (row.get("country_a_iso") or "").strip().upper()
        iso_b = (row.get("country_b_iso") or "").strip().upper()
        pair_key = (row.get("pair_key") or "").strip().upper()
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        source_domain = (row.get("source_domain") or "").strip()
        category_raw = (row.get("category") or "").strip()
        tone_raw = (row.get("tone") or "").strip().lower()

        if iso_a not in ACTIVE_COUNTRY_CODES or iso_b not in ACTIVE_COUNTRY_CODES:
            raise ValueError(f"Line {line_number}: country codes must be active 10-country ISO codes.")
        if iso_a == iso_b:
            raise ValueError(f"Line {line_number}: country_a_iso and country_b_iso must differ.")
        pair_codes = pair_key.split("_")
        if (
            len(pair_codes) != 2
            or pair_codes[0] == pair_codes[1]
            or set(pair_codes) != {iso_a, iso_b}
        ):
            raise ValueError(
                f"Line {line_number}: pair_key {pair_key!r} must contain the same unordered country pair."
            )
        if not url:
            raise ValueError(f"Line {line_number}: URL is required.")
        if not title:
            raise ValueError(f"Line {line_number}: title is required.")
        if tone_raw not in TONE_VALUES:
            raise ValueError(f"Line {line_number}: invalid tone {tone_raw!r}.")
        if category_raw not in CATEGORY_VALUES:
            raise ValueError(f"Line {line_number}: invalid category {category_raw!r}.")

        published_at = self._parse_publication_timestamp(row, line_number)
        if not SNAPSHOT_START <= published_at.date() <= SNAPSHOT_END:
            raise ValueError(
                f"Line {line_number}: publication date must fall within the "
                f"historical snapshot window ({SNAPSHOT_START} to {SNAPSHOT_END})."
            )

        if not source_domain:
            source_domain = urlparse(url).netloc
        if not source_domain:
            raise ValueError(f"Line {line_number}: source_domain is required or must be derivable from URL.")

        return {
            "country_a": countries[iso_a],
            "country_b": countries[iso_b],
            "title": title[:500],
            "url": url[:500],
            "source_domain": source_domain[:255],
            "published_at": published_at,
            "tone": TONE_VALUES[tone_raw],
            "category": CATEGORY_VALUES[category_raw],
        }

    def _parse_publication_timestamp(self, row, line_number):
        exact = (row.get("published_at_utc") or "").strip()
        published_date = (row.get("published_date") or "").strip()
        if exact:
            value = exact.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: invalid published_at_utc {exact!r}.") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        if not published_date:
            raise ValueError(f"Line {line_number}: published_date is required when exact timestamp is absent.")
        try:
            parsed_date = date.fromisoformat(published_date)
        except ValueError as exc:
            raise ValueError(f"Line {line_number}: invalid published_date {published_date!r}.") from exc

        return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    def _seed_indicators(self, csv_path):
        if not csv_path.exists():
            raise CommandError(f"Country indicator CSV not found: {csv_path}")

        countries = {
            country.iso_code: country
            for country in Country.objects.filter(iso_code__in=ACTIVE_COUNTRY_CODES, is_active=True)
        }

        rows_read = 0
        inserted = 0
        updated = 0
        skipped = 0
        rejected = 0
        errors = []
        covered_with_value = set()

        for row in load_indicator_rows(csv_path):
            rows_read += 1
            iso = row["iso_code"]
            code = row["indicator_code"]
            if not code or iso not in countries:
                rejected += 1
                errors.append(f"Row {rows_read}: invalid or unknown country/indicator ({iso} / {code!r}).")
                continue
            if row["value"] is None or row["year"] is None:
                skipped += 1
                continue

            covered_with_value.add((iso, code))
            _, created = CountryIndicator.objects.update_or_create(
                country=countries[iso],
                indicator_code=code,
                year=row["year"],
                defaults={
                    "indicator_name": row["indicator_name"] or code,
                    "value": row["value"],
                },
            )
            if created:
                inserted += 1
            else:
                updated += 1

        pruned_stale = self._prune_stale_indicators(countries, covered_with_value)

        if errors:
            self.stdout.write(self.style.WARNING("Indicator validation errors:"))
            for error in errors:
                self.stdout.write(f"  - {error}")

        return {
            "Indicator rows read": rows_read,
            "Indicator rows inserted": inserted,
            "Indicator rows updated": updated,
            "Indicator rows skipped (no value/year)": skipped,
            "Indicator rows rejected": rejected,
            "Stale indicator rows pruned": pruned_stale,
        }

    def _prune_stale_indicators(self, active_countries, covered_with_value):
        """Remove active-country indicator codes with no valid CSV observation.

        The indicator CSV is authoritative for active-country code coverage. If a
        country/code has no usable value, prune its existing observations so stale
        values cannot appear current. Preserve all years for codes still covered
        by a valid value, and leave inactive-country records and other tables alone.
        """
        active_country_ids = [country.pk for country in active_countries.values()]
        rows = CountryIndicator.objects.filter(country_id__in=active_country_ids).select_related("country")
        pruned = 0
        for row in rows.iterator():
            if (row.country.iso_code, row.indicator_code) not in covered_with_value:
                row.delete()
                pruned += 1
        return pruned
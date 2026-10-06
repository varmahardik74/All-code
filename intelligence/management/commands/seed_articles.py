"""Import verified seeded historical articles for local demo/offline mode."""

import csv
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from intelligence.models import Article, Country
from intelligence.services.narrative import CATEGORIES
from intelligence.services.pairs import canonical_pair_key, order_countries_by_iso, unordered_pair_q


ACTIVE_COUNTRY_CODES = {"CHN", "DEU", "GBR", "IND", "IRN", "ISR", "JPN", "PAK", "RUS", "USA"}
SEED_START = date(2026, 9, 28)
SEED_END = date(2026, 10, 6)

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
    help = "Import verified historical seed articles from between_nations_seed_articles_v1.csv."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            type=str,
            default=str(settings.BASE_DIR / "between_nations_seed_articles_v1.csv"),
            help="Path to the seed article CSV.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["path"])
        if not csv_path.exists():
            raise CommandError(f"Seed article CSV not found: {csv_path}")

        countries = {
            country.iso_code: country
            for country in Country.objects.filter(iso_code__in=ACTIVE_COUNTRY_CODES, is_active=True)
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
                    article_data = self._validate_row(row, line_number, countries)
                except ValueError as exc:
                    rejected += 1
                    errors.append(str(exc))
                    continue

                country_a, country_b = order_countries_by_iso(
                    article_data["country_a"], article_data["country_b"]
                )
                unique_pairs.add(canonical_pair_key(country_a.iso_code, country_b.iso_code))

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

        if errors:
            self.stdout.write(self.style.WARNING("Validation errors:"))
            for error in errors:
                self.stdout.write(f"  - {error}")

        total_seeded = Article.objects.filter(
            url__in=self._csv_urls(csv_path),
        ).count()
        self.stdout.write(self.style.SUCCESS("Seed article import complete."))
        self.stdout.write(f"Rows read: {rows_read}")
        self.stdout.write(f"Rows inserted: {inserted}")
        self.stdout.write(f"Rows skipped as duplicates: {duplicates}")
        self.stdout.write(f"Rows rejected: {rejected}")
        self.stdout.write(f"Unique pairs represented: {len(unique_pairs)}")
        self.stdout.write(f"Total seeded articles: {total_seeded}")

        if rejected:
            raise CommandError(f"{rejected} seed article row(s) failed validation.")

    def _validate_row(self, row, line_number, countries):
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
        if not SEED_START <= published_at.date() <= SEED_END:
            raise ValueError(
                f"Line {line_number}: publication date must fall between {SEED_START} and {SEED_END}."
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

    def _csv_urls(self, csv_path):
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            return [
                (row.get("url") or "").strip()[:500]
                for row in csv.DictReader(handle)
                if (row.get("url") or "").strip()
            ]

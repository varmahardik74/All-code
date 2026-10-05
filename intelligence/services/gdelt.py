"""GDELT DOC 2.0 API communication, parsing, and article persistence.

This service queries the GDELT DOC 2.0 API for bilateral news coverage
between two selected countries, processes volume and tone timelines,
and stores recent article records in the MySQL database.

ANALYSIS WINDOW: Last 7 days only.
No historical comparison window is queried here.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import time
from urllib.parse import urlparse
import re

import requests
from django.db import transaction
from django.utils.dateparse import parse_datetime

from intelligence.models import Article, Country

logger = logging.getLogger(__name__)

GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_TIMEOUT = 12
DEFAULT_USER_AGENT = "BetweenNations/1.0 (Geopolitical Analysis Project)"

# The single fixed analysis window used throughout the project
ANALYSIS_WINDOW_DAYS = 7

# Centralized title aliases used for deterministic bilateral relevance checks.
COUNTRY_ALIASES = {
    "USA": [r"\bunited states\b", r"\bu\.?s\.?a?\b", r"\bamerica\w*"],
    "CHN": [r"\bchina\b", r"\bchinese\b", r"\bbeijing\b"],
    "IND": [r"\bindia\b", r"\bindian\b", r"\bnew delhi\b"],
    "IRN": [r"\biran\b", r"\biranian\b", r"\btehran\b"],
    "SAU": [r"\bsaudi arabia\b", r"\bsaudi\b", r"\briyadh\b"],
    "TUR": [r"\bturkey\b", r"\bturkish\b", r"\btürkiye\b", r"\bankara\b"],
    "FRA": [r"\bfrance\b", r"\bfrench\b", r"\bparis\b"],
    "DEU": [r"\bgermany\b", r"\bgerman\b", r"\bberlin\b"],
    "BRA": [r"\bbrazil\b", r"\bbrazilian\b", r"\bbrasilia\b"],
    "MEX": [r"\bmexico\b", r"\bmexican\b"],
}

RELATIONSHIP_TERMS = re.compile(
    r"\b(bilateral|diplomatic|diplomacy|summit|talks|meeting|trade|tariff|border|"
    r"relations?|agreement|treaty|sanctions?|military|security|cooperation|tension|clash)\b",
    re.IGNORECASE,
)


class GDELTError(Exception):
    """Base exception for GDELT API errors."""


class GDELTRateLimitError(GDELTError):
    """Raised when GDELT responds with HTTP 429 or rate-limiting message."""


class GDELTConnectionError(GDELTError):
    """Raised when connecting to GDELT fails or times out."""


class GDELTParseError(GDELTError):
    """Raised when GDELT response cannot be parsed."""


def build_bilateral_query(country_a: Country, country_b: Country) -> str:
    """Build a bilateral query requiring BOTH countries to appear in coverage.

    Example result: '"India" "China"'
    """
    name_a = country_a.name.strip()
    name_b = country_b.name.strip()
    relationship_terms = " OR ".join(
        '"' + term + '"'
        for term in ("bilateral", "diplomatic", "trade", "border", "security", "relations")
    )
    return f'"{name_a}" "{name_b}" ({relationship_terms})'


def _country_patterns(country: Country) -> list[re.Pattern]:
    patterns = COUNTRY_ALIASES.get(country.iso_code, [rf"\b{re.escape(country.name)}\b"])
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def is_bilateral_relevant(article_title: str, country_a: Country, country_b: Country) -> bool:
    """Return whether a title clearly concerns both selected countries."""
    if not article_title:
        return False
    has_a = any(pattern.search(article_title) for pattern in _country_patterns(country_a))
    has_b = any(pattern.search(article_title) for pattern in _country_patterns(country_b))
    return has_a and has_b and bool(RELATIONSHIP_TERMS.search(article_title))


def _retry_delay(response, backoff: float, attempt: int) -> float:
    """Return a bounded delay, honoring Retry-After when it is present."""
    retry_after = response.headers.get("Retry-After") if response is not None else None
    try:
        requested = float(retry_after)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            requested = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            requested = backoff * (attempt + 1)
    return min(max(requested, 0.0), 30.0)


def _request_gdelt(params: dict, timeout: int = DEFAULT_TIMEOUT, retries: int = 1, backoff: float = 5.0) -> dict:
    """Execute a request against the GDELT DOC 2.0 API with retry and rate-limit handling."""
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    last_exception = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(
                GDELT_DOC_ENDPOINT,
                params=params,
                headers=headers,
                timeout=timeout,
            )

            if response.status_code == 429:
                if attempt < retries:
                    time.sleep(_retry_delay(response, backoff, attempt))
                    continue
                raise GDELTRateLimitError("GDELT API rate limit exceeded (HTTP 429).")

            if response.status_code != 200:
                body_sample = response.text[:200].strip()
                if "limit requests" in body_sample.lower():
                    if attempt < retries:
                        time.sleep(_retry_delay(response, backoff, attempt))
                        continue
                    raise GDELTRateLimitError(f"GDELT rate limit: {body_sample}")
                raise GDELTError(f"GDELT returned HTTP {response.status_code}: {body_sample}")

            text_body = response.text.strip()

            if "limit requests" in text_body.lower():
                if attempt < retries:
                    time.sleep(_retry_delay(response, backoff, attempt))
                    continue
                raise GDELTRateLimitError("GDELT returned rate-limit instruction in response body.")

            if not text_body:
                return {}

            try:
                parsed = response.json()
            except Exception as json_err:
                if text_body.startswith("<html") or text_body.startswith("<!DOCTYPE"):
                    raise GDELTParseError("GDELT returned HTML page instead of JSON.")
                raise GDELTParseError(f"Failed to parse GDELT JSON: {json_err}") from json_err
            if not isinstance(parsed, dict):
                raise GDELTParseError("GDELT response JSON must be an object.")
            return parsed

        except (requests.Timeout, requests.ConnectionError) as conn_err:
            last_exception = conn_err
            if attempt < retries:
                time.sleep(backoff)
                continue
            raise GDELTConnectionError(f"Unable to connect to GDELT API: {conn_err}") from conn_err
        except GDELTError:
            raise
        except Exception as exc:
            raise GDELTError(f"Unexpected error querying GDELT: {exc}") from exc

    if last_exception:
        raise GDELTConnectionError(f"GDELT request failed after retries: {last_exception}")
    raise GDELTError("GDELT request failed without response.")


def get_recent_bilateral_articles(
    country_a: Country,
    country_b: Country,
    max_records: int = 150,
) -> list[dict]:
    """Retrieve recent bilateral news articles from the last 7 days.

    Returns a list of parsed article dictionaries.
    """
    query = build_bilateral_query(country_a, country_b)
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(max_records, 250),
        "timespan": f"{ANALYSIS_WINDOW_DAYS}d",
    }

    raw = _request_gdelt(params)
    articles = []

    if not isinstance(raw, dict) or "articles" not in raw:
        return articles

    for item in raw.get("articles", []):
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        if not url or not title:
            continue

        domain = item.get("domain", "")
        if not domain:
            try:
                domain = urlparse(url).netloc
            except Exception:
                domain = "unknown"

        seendate = item.get("seendate")
        pub_dt = None
        if seendate:
            pub_dt = parse_datetime(seendate)
            if pub_dt is None:
                for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        pub_dt = datetime.strptime(seendate, fmt).replace(tzinfo=timezone.utc)
                        break
                    except (ValueError, TypeError):
                        continue
        if pub_dt is None:
            pub_dt = datetime.now(timezone.utc)

        tone = item.get("tone")
        try:
            tone_val = float(tone) if tone is not None else None
        except (ValueError, TypeError):
            tone_val = None

        article = {
            "title": title[:500],
            "url": url[:500],
            "source_domain": domain[:255],
            "published_at": pub_dt,
            "tone": tone_val,
        }
        if is_bilateral_relevant(article["title"], country_a, country_b):
            articles.append(article)

    return articles


def store_articles(
    country_a: Country,
    country_b: Country,
    articles_data: list[dict],
    classifier_fn=None,
) -> int:
    """Store retrieved articles in the database idempotently.

    Uses URL as the primary duplicate prevention mechanism.
    Assigns each article a category via classifier_fn if provided.
    Returns the count of newly inserted articles.
    """
    if not articles_data:
        return 0

    urls = [a["url"] for a in articles_data if a.get("url")]
    existing_urls = set(
        Article.objects.filter(url__in=urls).values_list("url", flat=True)
    )

    new_objects = []
    for item in articles_data:
        url = item["url"]
        if url in existing_urls:
            continue

        category = None
        if classifier_fn:
            try:
                category = classifier_fn(item["title"])
            except Exception:
                category = None

        new_objects.append(
            Article(
                country_a=country_a,
                country_b=country_b,
                title=item["title"],
                url=url,
                source_domain=item["source_domain"],
                published_at=item["published_at"],
                tone=item.get("tone"),
                category=category,
            )
        )
        existing_urls.add(url)

    if new_objects:
        with transaction.atomic():
            Article.objects.bulk_create(new_objects, ignore_conflicts=True)

    return len(new_objects)

"""Country-pair normalization helpers."""

from django.db.models import Q


def canonical_pair_key(iso_a: str, iso_b: str) -> str:
    """Return the deterministic unordered pair key used by seeded data."""
    codes = sorted([iso_a.upper().strip(), iso_b.upper().strip()])
    return f"{codes[0]}_{codes[1]}"


def order_countries_by_iso(country_a, country_b):
    """Return countries in deterministic ISO order for storage."""
    return tuple(sorted([country_a, country_b], key=lambda country: country.iso_code))


def unordered_pair_q(country_a, country_b) -> Q:
    """Build a reusable unordered pair filter for Article-style country fields."""
    return (
        Q(country_a=country_a, country_b=country_b)
        | Q(country_a=country_b, country_b=country_a)
    )

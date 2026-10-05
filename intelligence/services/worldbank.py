"""World Bank Indicators API v2 service.

Retrieves official national development indicators for the fixed country universe,
extracts the latest non-empty observations, and persists them into the MySQL database.
"""

from datetime import datetime, timedelta, timezone
import logging
import requests
from django.db import transaction

from intelligence.models import Country, CountryIndicator

logger = logging.getLogger(__name__)

WORLD_BANK_API_BASE = "https://api.worldbank.org/v2/country"
DEFAULT_TIMEOUT = 12
CACHE_MAX_AGE_DAYS = 7

# Exactly the 10 locked indicators specified in project architecture:
COUNTRY_INDICATORS = {
    "NY.GDP.MKTP.CD": {
        "name": "GDP (current US$)",
        "short_name": "GDP",
        "unit": "current USD",
        "description": "Gross domestic product at purchaser's prices in current US dollars.",
    },
    "NY.GDP.MKTP.KD.ZG": {
        "name": "GDP Growth Rate (annual %)",
        "short_name": "GDP Growth Rate",
        "unit": "%",
        "description": "Annual percentage growth rate of GDP at market prices.",
    },
    "NY.GDP.PCAP.CD": {
        "name": "GDP per Capita (current US$)",
        "short_name": "GDP per Capita",
        "unit": "current USD",
        "description": "Gross domestic product divided by midyear population.",
    },
    "SP.POP.TOTL": {
        "name": "Population, total",
        "short_name": "Population",
        "unit": "people",
        "description": "Total midyear population count.",
    },
    "SP.POP.GROW": {
        "name": "Population Growth Rate (annual %)",
        "short_name": "Population Growth Rate",
        "unit": "%",
        "description": "Annual exponential growth rate of population.",
    },
    "FP.CPI.TOTL.ZG": {
        "name": "Inflation Rate (consumer prices, annual %)",
        "short_name": "Inflation Rate",
        "unit": "%",
        "description": "Annual percentage change in the cost of the average consumer basket.",
    },
    "SL.UEM.TOTL.ZS": {
        "name": "Unemployment Rate (% of total labor force)",
        "short_name": "Unemployment Rate",
        "unit": "%",
        "description": "Share of labor force without work but available and seeking employment.",
    },
    "NE.TRD.GNFS.ZS": {
        "name": "Trade (% of GDP)",
        "short_name": "Trade (% of GDP)",
        "unit": "%",
        "description": "Sum of exports and imports of goods and services as a share of GDP.",
    },
    "MS.MIL.XPND.GD.ZS": {
        "name": "Military Expenditure (% of GDP)",
        "short_name": "Military Expenditure",
        "unit": "%",
        "description": "Military expenditure as a percentage of gross domestic product (chosen for cross-country comparability).",
    },
    "NE.EXP.GNFS.ZS": {
        "name": "Exports of Goods and Services (% of GDP)",
        "short_name": "Exports of Goods and Services",
        "unit": "%",
        "description": "Exports of goods and services as a share of GDP.",
    },
}


class WorldBankError(Exception):
    """Base exception for World Bank API errors."""


class WorldBankConnectionError(WorldBankError):
    """Raised when connection to World Bank API fails."""


class WorldBankParseError(WorldBankError):
    """Raised when World Bank API response cannot be parsed."""


def fetch_country_indicator_from_api(
    country_iso: str,
    indicator_code: str,
    timeout: int = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> dict | None:
    """Fetch the latest available non-empty observation for an indicator from World Bank API.

    Returns dict with keys:
    - indicator_code
    - indicator_name
    - value
    - year
    - country_iso
    or None if no non-empty observation is available.
    """
    url = f"{WORLD_BANK_API_BASE}/{country_iso}/indicator/{indicator_code}"
    params = {
        "format": "json",
        "per_page": 20,
    }

    client = session or requests
    try:
        response = client.get(url, params=params, timeout=timeout)
        if response.status_code != 200:
            raise WorldBankError(f"World Bank API HTTP {response.status_code}: {response.text[:200]}")
        payload = response.json()
    except (requests.Timeout, requests.ConnectionError) as conn_err:
        raise WorldBankConnectionError(f"Failed to connect to World Bank API: {conn_err}") from conn_err
    except Exception as exc:
        raise WorldBankParseError(f"Invalid World Bank API response: {exc}") from exc

    if not isinstance(payload, list) or len(payload) < 2:
        return None

    entries = payload[1]
    if not isinstance(entries, list):
        return None

    # Search for latest non-empty value (sorted descending by date by default)
    for item in entries:
        raw_val = item.get("value")
        if raw_val is not None:
            try:
                numeric_val = float(raw_val)
                year_str = str(item.get("date", "")).strip()
                year = int(year_str) if year_str.isdigit() else None
                if year is None:
                    continue

                meta = COUNTRY_INDICATORS.get(indicator_code, {})
                name = meta.get("name") or (item.get("indicator") or {}).get("value", indicator_code)

                return {
                    "indicator_code": indicator_code,
                    "indicator_name": name,
                    "value": numeric_val,
                    "year": year,
                    "country_iso": country_iso,
                }
            except (ValueError, TypeError):
                continue

    return None


def fetch_all_country_indicators(
    country: Country,
    force_refresh: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, dict]:
    """Retrieve all 10 configured indicators for a country.

    Checks the database first for recent observations (within CACHE_MAX_AGE_DAYS).
    If missing or stale (or if force_refresh is True), fetches from World Bank API
    and persists them into CountryIndicator.

    Returns dict mapping indicator_code -> dict with indicator data.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=CACHE_MAX_AGE_DAYS)
    existing_qs = CountryIndicator.objects.filter(country=country)

    if not force_refresh:
        recent_count = existing_qs.filter(fetched_at__gte=cutoff).count()
        if recent_count >= len(COUNTRY_INDICATORS):
            # Return cached indicators from DB
            latest_db = {}
            for ind in existing_qs.order_by("indicator_code", "-year"):
                if ind.indicator_code not in latest_db:
                    latest_db[ind.indicator_code] = {
                        "indicator_code": ind.indicator_code,
                        "indicator_name": ind.indicator_name,
                        "value": ind.value,
                        "year": ind.year,
                        "country_iso": country.iso_code,
                    }
            return latest_db

    results = {}
    with requests.Session() as session:
        for code, meta in COUNTRY_INDICATORS.items():
            obs = None
            try:
                obs = fetch_country_indicator_from_api(
                    country.iso_code,
                    code,
                    timeout=timeout,
                    session=session,
                )
            except WorldBankError as wb_err:
                logger.warning("World Bank indicator %s fetch error for %s: %s", code, country.iso_code, wb_err)
                obs = None

            if obs is not None:
                CountryIndicator.objects.update_or_create(
                    country=country,
                    indicator_code=code,
                    year=obs["year"],
                    defaults={
                        "indicator_name": obs["indicator_name"],
                        "value": obs["value"],
                    },
                )
                results[code] = obs
            else:
                # Check if an earlier stored value exists in the DB
                latest_stored = existing_qs.filter(indicator_code=code).order_by("-year").first()
                if latest_stored:
                    results[code] = {
                        "indicator_code": code,
                        "indicator_name": latest_stored.indicator_name,
                        "value": latest_stored.value,
                        "year": latest_stored.year,
                        "country_iso": country.iso_code,
                    }
                else:
                    # Retain None for missing data (DO NOT convert to 0)
                    results[code] = {
                        "indicator_code": code,
                        "indicator_name": meta["name"],
                        "value": None,
                        "year": None,
                        "country_iso": country.iso_code,
                    }

    return results

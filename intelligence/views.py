import base64
from datetime import datetime, timezone

import pandas as pd
import logging
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ComparisonForm
from .models import Country
from .services.analysis import run_country_pair_analysis
from .services.charts import (
    generate_activity_timeline_chart,
    generate_indicator_comparison_chart,
    generate_narrative_chart,
)

logger = logging.getLogger(__name__)


def home(request):
    if request.method == "POST":
        form = ComparisonForm(request.POST)
        if form.is_valid():
            country_a = form.cleaned_data["country_a"]
            country_b = form.cleaned_data["country_b"]
            return redirect("comparison", iso_a=country_a.iso_code, iso_b=country_b.iso_code)
    else:
        form = ComparisonForm()
    return render(request, "intelligence/home.html", {"form": form})


def comparison(request, iso_a, iso_b):
    country_a = get_object_or_404(Country, iso_code=iso_a, is_active=True)
    country_b = get_object_or_404(Country, iso_code=iso_b, is_active=True)
    if country_a.pk == country_b.pk:
        form = ComparisonForm(initial={"country_a": country_a, "country_b": country_b})
        form.add_error(None, "Select two different countries.")
        return render(request, "intelligence/home.html", {"form": form}, status=400)
    try:
        result = run_country_pair_analysis(country_a, country_b)
        if not result["metadata"].get("geopolitical_data_available", True):
            return render(request, "intelligence/comparison.html", {
                "country_a": country_a,
                "country_b": country_b,
                "error": "Geopolitical data temporarily unavailable. Please try again later.",
            })
        activity = result["news_activity"]
        now = datetime.now(timezone.utc)
        dates = pd.date_range(end=now.date(), periods=7, freq="D")
        timeline = pd.DataFrame({"date": dates, "volume": activity.get("daily_article_count", [])})
        charts = {
            "activity": _chart_data(generate_activity_timeline_chart(timeline, country_a.name, country_b.name)) if result["metadata"].get("gdelt_status") != "success_no_data" else None,
            "narrative": _chart_data(generate_narrative_chart(result["narrative_shift"], country_a.name, country_b.name)) if result["narrative_shift"] else None,
            "indicators": _chart_data(generate_indicator_comparison_chart(result["indicators"].get(country_a.iso_code, {}), result["indicators"].get(country_b.iso_code, {}), country_a.name, country_b.name)),
        }
        indicators_a = result["indicators"].get(country_a.iso_code, {})
        indicators_b = result["indicators"].get(country_b.iso_code, {})
        indicator_rows = []
        for code, item_a in indicators_a.items():
            item_b = indicators_b.get(code, {})
            indicator_rows.append({
                "name": item_a.get("indicator_name", code),
                "a": item_a,
                "b": item_b,
                "a_display": _format_indicator(code, item_a.get("value")),
                "b_display": _format_indicator(code, item_b.get("value")),
            })
        context = {"country_a": country_a, "country_b": country_b, "result": result, "charts": charts, "indicator_rows": indicator_rows}
    except Exception:
        logger.exception("Unable to render comparison dashboard")
        context = {"country_a": country_a, "country_b": country_b, "error": "The analysis is temporarily unavailable. Please try again."}
    return render(request, "intelligence/comparison.html", context)


def _chart_data(content):
    return base64.b64encode(content).decode("ascii")


def _format_indicator(code, value):
    if value is None:
        return "N/A"
    if code in {"NY.GDP.MKTP.CD", "NY.GDP.PCAP.CD"}:
        amount = float(value)
        if code == "NY.GDP.MKTP.CD" and abs(amount) >= 1_000_000_000_000:
            return f"${amount / 1_000_000_000_000:.2f} T"
        if abs(amount) >= 1_000_000_000:
            return f"${amount / 1_000_000_000:.2f} B"
        return f"${amount:,.2f}"
    if code == "SP.POP.TOTL":
        amount = float(value)
        if amount >= 1_000_000_000:
            return f"{amount / 1_000_000_000:.2f} B"
        return f"{amount / 1_000_000:.2f} M"
    return f"{float(value):,.2f}"

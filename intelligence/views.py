"""Web layer: comparison form and the two dashboard pages."""

import logging

from django import forms
from django.shortcuts import get_object_or_404, redirect, render

from .analysis import run_country_pair_analysis
from .models import Country
from .seed import CANONICAL_INDICATORS

logger = logging.getLogger(__name__)


class ComparisonForm(forms.Form):
    country_a = forms.ModelChoiceField(
        queryset=Country.objects.filter(is_active=True),
        label="Country A",
        empty_label="Select a country",
    )
    country_b = forms.ModelChoiceField(
        queryset=Country.objects.filter(is_active=True),
        label="Country B",
        empty_label="Select a country",
    )

    def clean(self):
        cleaned = super().clean()
        country_a = cleaned.get("country_a")
        country_b = cleaned.get("country_b")
        if country_a and country_b and country_a.pk == country_b.pk:
            raise forms.ValidationError("Select two different countries.")
        return cleaned


def home(request):
    if request.method == "POST":
        form = ComparisonForm(request.POST)
        if form.is_valid():
            return redirect(
                "comparison",
                iso_a=form.cleaned_data["country_a"].iso_code,
                iso_b=form.cleaned_data["country_b"].iso_code,
            )
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
        indicators_a = result["indicators"].get(country_a.iso_code, {})
        indicators_b = result["indicators"].get(country_b.iso_code, {})
        indicator_rows = [
            {
                "name": indicators_a.get(code, {}).get("indicator_name", code),
                "a": indicators_a.get(code, {}),
                "b": indicators_b.get(code, {}),
                "a_display": _format_indicator(code, indicators_a.get(code, {}).get("value")),
                "b_display": _format_indicator(code, indicators_b.get(code, {}).get("value")),
            }
            for code in CANONICAL_INDICATORS
        ]
        context = {
            "country_a": country_a,
            "country_b": country_b,
            "result": result,
            "indicator_rows": indicator_rows,
        }
    except Exception:
        logger.exception("Unable to render comparison dashboard")
        context = {
            "country_a": country_a,
            "country_b": country_b,
            "error": "The analysis is temporarily unavailable. Please try again.",
        }
    return render(request, "intelligence/comparison.html", context)


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

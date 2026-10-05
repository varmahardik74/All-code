from django import forms

from .models import Country


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

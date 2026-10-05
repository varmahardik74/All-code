"""Management command to fetch and persist data for a selected country pair."""

from django.core.management.base import BaseCommand, CommandError

from intelligence.models import Country
from intelligence.services.analysis import run_country_pair_analysis
from intelligence.services.worldbank import COUNTRY_INDICATORS


class Command(BaseCommand):
    help = "Fetch and persist external data (GDELT & World Bank) for a country pair."

    def add_arguments(self, parser):
        parser.add_argument("iso_a", type=str, help="ISO code of first country (e.g. IND)")
        parser.add_argument("iso_b", type=str, help="ISO code of second country (e.g. CHN)")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force fresh retrieval bypassing cache.",
        )

    def handle(self, *args, **options):
        iso_a = options["iso_a"].upper().strip()
        iso_b = options["iso_b"].upper().strip()

        if iso_a == iso_b:
            raise CommandError("Please select two distinct country ISO codes.")

        try:
            country_a = Country.objects.get(iso_code=iso_a, is_active=True)
            country_b = Country.objects.get(iso_code=iso_b, is_active=True)
        except Country.DoesNotExist as exc:
            raise CommandError(f"Country not found in universe: {exc}") from exc

        self.stdout.write(
            self.style.NOTICE(f"Fetching data for {country_a.name} ({iso_a}) × {country_b.name} ({iso_b})...")
        )

        result = run_country_pair_analysis(country_a, country_b, force_refresh=options["force"])

        # Display concise summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 45))
        self.stdout.write(self.style.SUCCESS(f"BETWEEN NATIONS DATA ENGINE"))
        self.stdout.write(self.style.SUCCESS(f"{country_a.name} × {country_b.name}"))
        self.stdout.write(self.style.SUCCESS("=" * 45))

        ind_a = result["indicators"].get(iso_a, {})
        ind_b = result["indicators"].get(iso_b, {})

        valid_a = sum(1 for v in ind_a.values() if v.get("value") is not None)
        valid_b = sum(1 for v in ind_b.values() if v.get("value") is not None)
        total_expected = len(COUNTRY_INDICATORS)

        self.stdout.write(f"\nCountry indicators loaded:")
        self.stdout.write(f"  {valid_a} / {total_expected} {country_a.name}")
        self.stdout.write(f"  {valid_b} / {total_expected} {country_b.name}")

        meta = result.get("metadata", {})
        self.stdout.write(f"\nArticles available in database:")
        self.stdout.write(f"  Recent (7d):   {meta.get('recent_article_count', 0)}")
        self.stdout.write(f"  Articles (7d): {meta.get('article_count', 0)}")
        self.stdout.write(f"  GDELT status:   {meta.get('gdelt_status')}")

        self.stdout.write(self.style.SUCCESS("\nData fetch completed successfully.\n"))

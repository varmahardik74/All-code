"""Management command to run and display the complete analysis for a country pair."""

from django.core.management.base import BaseCommand, CommandError

from intelligence.analysis import run_country_pair_analysis
from intelligence.models import Country
from intelligence.seeds import CANONICAL_INDICATORS, SNAPSHOT_LABEL


class Command(BaseCommand):
    help = "Analyze a country pair from the local historical snapshot."

    def add_arguments(self, parser):
        parser.add_argument("iso_a", type=str, help="ISO code of first country (e.g. IND)")
        parser.add_argument("iso_b", type=str, help="ISO code of second country (e.g. CHN)")

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
            self.style.NOTICE(f"Running analysis for {country_a.name} × {country_b.name}...")
        )

        res = run_country_pair_analysis(country_a, country_b)

        # Display concise, human-readable summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 48))
        self.stdout.write(self.style.SUCCESS("BETWEEN NATIONS"))
        self.stdout.write(self.style.SUCCESS(f"{country_a.name} × {country_b.name}"))
        self.stdout.write(self.style.SUCCESS("=" * 48))

        act = res["news_activity"]
        self.stdout.write(f"\nAnalysis Window: {SNAPSHOT_LABEL}")
        self.stdout.write("News source: local (no external API)")

        self.stdout.write(f"\nRecent Developments: {len(res['articles'])} articles shown")
        if res["metadata"].get("data_status") == "success_no_data":
            self.stdout.write("No bilateral coverage found for this pair in the snapshot.")
        elif act is not None:
            self.stdout.write("\nNews Activity")
            self.stdout.write(f"Average articles/day: {act['average_articles_per_day']:.1f}")
            self.stdout.write(f"Busiest day: Day {act['peak_day']} | Peak article count: {act['peak_article_count']:.0f}")

        nar = res["narrative_shift"]
        if nar is not None:
            self.stdout.write(f"\nNarrative Shift: {nar['shift_score']:.1f}/100")
            self.stdout.write(f"Dominant later category: {nar['dominant_later_category']}")
            self.stdout.write(f"Largest positive shift: {nar['largest_positive_shift']}")
            self.stdout.write(f"Interpretation: {nar['interpretation']}")

        ind_a = res["indicators"].get(iso_a, {})
        ind_b = res["indicators"].get(iso_b, {})
        valid_a = sum(1 for v in ind_a.values() if v.get("value") is not None)
        valid_b = sum(1 for v in ind_b.values() if v.get("value") is not None)
        total_expected = len(CANONICAL_INDICATORS)

        self.stdout.write("\nCountry Indicators")
        self.stdout.write(f"  {valid_a} / {total_expected} {country_a.name}")
        self.stdout.write(f"  {valid_b} / {total_expected} {country_b.name}")

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 48 + "\n"))
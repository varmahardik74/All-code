from django.core.management.base import BaseCommand

from intelligence.models import Country

COUNTRIES = [
    ("United States", "USA"),
    ("China", "CHN"),
    ("Russia", "RUS"),
    ("India", "IND"),
    ("Iran", "IRN"),
    ("Saudi Arabia", "SAU"),
    ("Turkey", "TUR"),
    ("Egypt", "EGY"),
    ("United Arab Emirates", "ARE"),
    ("France", "FRA"),
    ("United Kingdom", "GBR"),
    ("Germany", "DEU"),
    ("Japan", "JPN"),
    ("South Korea", "KOR"),
    ("Australia", "AUS"),
    ("Canada", "CAN"),
    ("Brazil", "BRA"),
    ("South Africa", "ZAF"),
    ("Mexico", "MEX"),
    ("Indonesia", "IDN"),
]


class Command(BaseCommand):
    help = "Idempotently seed the fixed 20-country universe."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
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

from django.core.management.base import BaseCommand

from intelligence.models import Country

COUNTRIES = [
    ("China", "CHN"),
    ("India", "IND"),
    ("Iran", "IRN"),
    ("Russia", "RUS"),
    ("Germany", "DEU"),
    ("Japan", "JPN"),
    ("United Kingdom", "GBR"),
    ("United States", "USA"),
    ("Pakistan", "PAK"),
    ("Israel", "ISR"),
]


class Command(BaseCommand):
    help = "Idempotently seed the fixed 10-country universe."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        active_codes = {iso_code for _, iso_code in COUNTRIES}
        Country.objects.exclude(iso_code__in=active_codes).update(is_active=False)
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

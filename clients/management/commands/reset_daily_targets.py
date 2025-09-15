from django.core.management.base import BaseCommand
from django.utils import timezone
from clients.models import Target

class Command(BaseCommand):
    help = "Reset daily progress/points for all daily targets"

    def handle(self, *args, **kwargs):
        today = timezone.now().date()

        # Reset only daily targets
        updated = Target.objects.filter(target_type="daily").update(
            achieved_value=0,
            points_value=0
        )

        self.stdout.write(
            self.style.SUCCESS(f"✅ Reset {updated} daily targets on {today}")
        )

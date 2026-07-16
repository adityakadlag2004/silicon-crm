# clients/management/commands/close_month.py
from django.core.management.base import BaseCommand
from django.utils.timezone import now

from clients.services import targets


class Command(BaseCommand):
    help = "Close the previous month and store employee performance into MonthlyTargetHistory"

    def handle(self, *args, **kwargs):
        today = now().date()

        # Figure out previous month
        if today.month == 1:
            year = today.year - 1
            month = 12
        else:
            year = today.year
            month = today.month - 1

        written = targets.close_month(year, month, log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"Monthly targets closed for {month}/{year} ({written} rows)"
        ))

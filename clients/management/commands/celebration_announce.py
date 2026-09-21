"""Tell the whole team who the month's winners were.

Runs on the 2nd (CRONJOBS) for the month just closed — a day's grace so
month-end sales still waiting on approval are counted. Idempotent.

    .venv/bin/python manage.py celebration_announce [--dry-run]
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.services import business_report


class Command(BaseCommand):
    help = "Push last month's celebration winners to every active employee."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Print the message, notify nobody.")

    def handle(self, *args, **opts):
        today = timezone.localdate()
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        msg = business_report.announcement(year, month)
        if not msg:
            self.stdout.write("No winners — nothing to announce.")
            return
        self.stdout.write(f"{msg[0]}\n{msg[1]}")
        sent = business_report.announce(year, month, dry_run=opts["dry_run"])
        self.stdout.write(self.style.SUCCESS(
            f"{'Would notify' if opts['dry_run'] else 'Notified'} {sent} employee(s)."))

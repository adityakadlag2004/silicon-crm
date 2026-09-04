"""Retirement-planning alerts for clients turning 40.

Daily (in CRONJOBS, 8:15 AM): every client whose 40th birthday is today gets
one high-priority ringing task, assigned to the mapped employee and every
active admin. ``services/client_alerts.py`` owns the rules.

``--backlog`` instead walks the clients already past 40 who have never been
alerted — the book that predates this feature, and the old profiles whose date
of birth is typed in on KYC Issues long after the fact. Dry run by default;
``--apply`` writes. It is deliberately NOT part of the daily run: firing it
automatically would raise hundreds of tasks the first morning after deploy.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.services import client_alerts


class Command(BaseCommand):
    help = "Raise retirement-planning tasks for clients turning 40 (or the 40+ backlog)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--backlog", action="store_true",
            help="Clients already past 40 with no alert yet, instead of today's birthdays.")
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the backlog's tasks (a backlog run is a dry run without it).")

    def handle(self, *args, **opts):
        today = timezone.localdate()

        if not opts["backlog"]:
            clients = list(client_alerts.turning_age_today(today))
            created, skipped = client_alerts.run_retirement(clients, today=today)
            self.stdout.write(self.style.SUCCESS(
                f"Retirement alerts: {created} raised, {skipped} already on file "
                f"({len(clients)} client(s) turned {client_alerts.TRIGGER_AGE} today)."
            ))
            return

        clients = client_alerts.retirement_backlog(today)
        if not opts["apply"]:
            self.stdout.write(
                f"DRY RUN — {len(clients)} client(s) past {client_alerts.TRIGGER_AGE} "
                f"with no alert yet. Re-run with --apply to raise them.")
            for c in clients[:20]:
                owner = (c.mapped_to.user.get_full_name() or c.mapped_to.user.username) \
                    if (c.mapped_to and c.mapped_to.user_id) else "UNMAPPED"
                self.stdout.write(f"  #{c.id} {c.name} — age {c.age} — {owner}")
            if len(clients) > 20:
                self.stdout.write(f"  … and {len(clients) - 20} more")
            return

        created, skipped = client_alerts.run_retirement(clients, today=today)
        self.stdout.write(self.style.SUCCESS(
            f"Backlog: {created} retirement alert(s) raised, {skipped} already on file."))

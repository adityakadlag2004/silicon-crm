"""Birthday tasks for every client whose birthday is today.

In CRONJOBS (daily 8:05 AM). One task per client per year, assigned to the
mapped employee AND every active admin as a multi-assignee group, deduped via
``Task.assign_group = "bday:<client>:<YYYY>"``. The three actions ride as a
checklist: wish them, send the valuation report, review the financial plan.

``services/client_alerts.py`` owns the rules; this is the daily trigger.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.services import client_alerts


class Command(BaseCommand):
    help = "Raise birthday-call tasks for clients with a birthday today."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="List today's birthdays without raising tasks.")

    def handle(self, *args, **opts):
        today = timezone.localdate()
        clients = list(client_alerts.birthdays_today(today))

        if opts["dry_run"]:
            self.stdout.write(f"DRY RUN — {len(clients)} birthday(s) today.")
            for c in clients:
                owner = (c.mapped_to.user.get_full_name() or c.mapped_to.user.username) \
                    if (c.mapped_to and c.mapped_to.user_id) else "UNMAPPED"
                raised = "already raised" if client_alerts.already_raised(
                    client_alerts.birthday_key(c, today.year)) else "would raise"
                self.stdout.write(f"  #{c.id} {c.name} — age {c.age} — {owner} — {raised}")
            return

        created, skipped = client_alerts.run_birthdays(clients, today=today)
        self.stdout.write(self.style.SUCCESS(
            f"Birthday tasks: {created} raised, {skipped} already on file "
            f"({len(clients)} birthday(s) today)."))

"""Generate employee milestones and nudge admins about them.

Runs daily. Two jobs:

  1. Derive this year's birthdays and work anniversaries from the Employee
     records (idempotent — re-running creates nothing new).
  2. Notify admins about anything landing today or tomorrow, and about
     anything that slipped past uncelebrated.

The nudge is the point. Pay is annual; being noticed is not, and without a
reminder the day passes and nobody says anything.

    .venv/bin/python manage.py employee_milestones
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Employee, EmployeeMilestone
from clients.services import people
from clients.services.tasks import create_notification


class Command(BaseCommand):
    help = "Generate employee milestones and remind admins to mark them."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would happen, notify nobody.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        today = timezone.localdate()

        created = people.generate_all(today)
        self.stdout.write(f"Generated {len(created)} new milestone(s).")

        # Landing today or tomorrow — enough warning to actually do something.
        soon = [m for m in people.upcoming(days=1)]
        overdue = list(people.missed())

        admins = [e.user for e in Employee.objects.filter(
            active=True, role=Employee.Role.ADMIN).select_related("user")]
        if not admins:
            self.stdout.write("No active admins to notify.")
            return

        sent = 0
        for milestone in soon:
            when = "today" if milestone.occurs_on == today else "tomorrow"
            for admin in admins:
                if milestone.employee.user_id == admin.id:
                    continue          # don't ask someone to celebrate themselves
                if dry:
                    continue
                create_notification(
                    admin,
                    f"🎂 {milestone.employee.short_name} — {milestone.get_kind_display().lower()} {when}",
                    milestone.title,
                    link="/clients/team/people/",
                    event=None,
                )
                sent += 1

        for milestone in overdue:
            days = abs(milestone.days_away)
            for admin in admins:
                if milestone.employee.user_id == admin.id or dry:
                    continue
                create_notification(
                    admin,
                    f"Missed: {milestone.employee.short_name}",
                    f"{milestone.title} was {days} day{'s' if days != 1 else ''} ago "
                    f"and hasn't been marked. A message still counts.",
                    link="/clients/team/people/",
                    event=None,
                )
                sent += 1

        verb = "Would send" if dry else "Sent"
        self.stdout.write(self.style.SUCCESS(
            f"{len(soon)} upcoming, {len(overdue)} missed. {verb} {sent} notification(s)."))

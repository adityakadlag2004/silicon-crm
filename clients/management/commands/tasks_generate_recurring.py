"""Generate Task instances from active RecurringTaskRules.

Runs daily via CRONJOBS. Delegates to services.tasks.generate_recurring, which
catches up on any missed periods and honours each rule's end conditions.
"""
from django.core.management.base import BaseCommand

from clients.services.tasks import generate_recurring


class Command(BaseCommand):
    help = "Spawn due task instances from recurring rules."

    def handle(self, *args, **options):
        count = generate_recurring()
        if count:
            self.stdout.write(self.style.SUCCESS(f"Generated {count} recurring task(s)."))

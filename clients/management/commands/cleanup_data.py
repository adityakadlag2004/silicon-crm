"""
Management command to clean up stale data and reduce database size.

Usage:
    python manage.py cleanup_data              # default: 90-day retention
    python manage.py cleanup_data --days 60    # custom retention

Add to crontab via django-crontab in settings.py.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Remove old read notifications, stale message logs, expired sessions, "
        "aged call-log entries, and finished call follow-ups to bound storage."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Delete records older than this many days (default: 90).",
        )
        parser.add_argument(
            "--call-days",
            type=int,
            default=365,
            help="Delete synced device call-log entries older than this many days (default: 365).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without actually deleting.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        dry_run = options["dry_run"]

        self.stdout.write(f"Cutoff date: {cutoff:%Y-%m-%d %H:%M}")

        # 1. Old READ notifications
        from clients.models import Notification

        old_notifs = Notification.objects.filter(is_read=True, created_at__lt=cutoff)
        count = old_notifs.count()
        if not dry_run:
            old_notifs.delete()
        self.stdout.write(f"  Notifications (read, old): {count} {'would be ' if dry_run else ''}deleted")

        # 2. Old sent/failed message logs
        from clients.models import MessageLog

        old_logs = MessageLog.objects.filter(
            status__in=["sent", "failed", "skipped"],
            created_at__lt=cutoff,
        )
        count = old_logs.count()
        if not dry_run:
            old_logs.delete()
        self.stdout.write(f"  Message logs (old):        {count} {'would be ' if dry_run else ''}deleted")

        # 3. Expired Django sessions
        from django.contrib.sessions.models import Session

        expired = Session.objects.filter(expire_date__lt=timezone.now())
        count = expired.count()
        if not dry_run:
            expired.delete()
        self.stdout.write(f"  Expired sessions:          {count} {'would be ' if dry_run else ''}deleted")

        # 4. Old device call-log entries. Every call on an employee's device is
        # synced (personal calls included), so a bounded retention window is a
        # data-minimization requirement, not just a storage saving. Analytics
        # only ever looks back one month.
        from clients.models import CallLogEntry

        call_cutoff = timezone.now() - timedelta(days=options["call_days"])
        old_calls = CallLogEntry.objects.filter(started_at__lt=call_cutoff)
        count = old_calls.count()
        if not dry_run:
            old_calls.delete()
        self.stdout.write(f"  Call log entries (old):    {count} {'would be ' if dry_run else ''}deleted")

        # 5. Finished call follow-ups (done/dismissed). Pending ones are kept
        # regardless of age — they still drive reminders and the popup.
        from clients.models import CallFollowUp

        old_followups = CallFollowUp.objects.filter(
            status__in=[CallFollowUp.STATUS_DONE, CallFollowUp.STATUS_DISMISSED],
            created_at__lt=cutoff,
        )
        count = old_followups.count()
        if not dry_run:
            old_followups.delete()
        self.stdout.write(f"  Call follow-ups (finished): {count} {'would be ' if dry_run else ''}deleted")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing was deleted."))
        else:
            self.stdout.write(self.style.SUCCESS("Cleanup complete."))

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
    help = "Remove old read notifications, stale message logs, and expired sessions to save storage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Delete records older than this many days (default: 90).",
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

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing was deleted."))
        else:
            self.stdout.write(self.style.SUCCESS("Cleanup complete."))

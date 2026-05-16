"""Auto-tag stale lead-sheet records as 'cold'.

A record is "stale" if it hasn't been updated in STALE_DAYS days, isn't
already tagged 'cold', and hasn't been converted to a client. Runs nightly
via CRONJOBS. Idempotent — re-running won't double-tag.

Usage:
    python manage.py autoclose_stale_leads [--days 90] [--tag cold] [--dry-run]
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from clients.models import LeadSheetRecord


class Command(BaseCommand):
    help = "Tag lead-sheet records that have been untouched for N days as 'cold'."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90,
                            help="Inactivity threshold in days (default 90).")
        parser.add_argument("--tag", type=str, default="cold",
                            help="Tag to apply (default 'cold').")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without writing.")

    def handle(self, *args, **opts):
        days = opts["days"]
        tag = opts["tag"].strip().lower()
        dry = opts["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        stale = (
            LeadSheetRecord.objects
            .filter(updated_at__lt=cutoff, converted_client__isnull=True)
            .exclude(tags__contains=[tag])
        )
        total = stale.count()
        if total == 0:
            self.stdout.write(f"No stale records older than {days} days. Nothing to do.")
            return

        if dry:
            self.stdout.write(f"[dry-run] Would tag {total} record(s) with '{tag}'.")
            return

        changed = 0
        for rec in stale.iterator(chunk_size=200):
            tags = list(rec.tags or [])
            if tag in tags:
                continue
            tags.append(tag)
            rec.tags = tags
            # Don't touch updated_at — bumping it would un-stale the row.
            rec.save(update_fields=["tags"])
            changed += 1

        self.stdout.write(self.style.SUCCESS(
            f"Tagged {changed} stale record(s) (> {days} days inactive) with '{tag}'."
        ))

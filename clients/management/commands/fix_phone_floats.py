"""Repair phone numbers a spreadsheet import stored as floats.

2,168 of 2,937 client rows carried "9423440791.0". That is worse than an
unmatchable string: every matcher in the app strips non-digits and takes the
last ten, so the number read as 4234407910 — a *different* number. Call
follow-ups showed a bare digit string instead of the client's name, duplicate
detection compared the wrong keys, and wa.me links pointed at nobody.

`Client.save()` normalises now, so nothing new can arrive in this shape; this
command is the one-off that fixes what is already stored.

Dry run by default — nothing is written without --apply. Idempotent: a second
run finds nothing. Only the trailing ".0" is removed; no other character of
any number is touched, and a row whose remainder is not all digits is skipped
rather than guessed at.

    manage.py fix_phone_floats               # show what would change
    manage.py fix_phone_floats --apply       # write it
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from clients.models import Client, Lead
from clients.utils.phone_utils import clean_phone

MODELS = ((Client, "name"), (Lead, "customer_name"))


class Command(BaseCommand):
    help = "Strip the Excel float tail ('.0') from stored phone numbers."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the changes (default is a dry run).")
        parser.add_argument("--limit", type=int, default=15,
                            help="How many examples to print (default 15).")

    def handle(self, *args, **opts):
        apply_changes = opts["apply"]
        total = 0

        for model, name_field in MODELS:
            label = model.__name__
            rows = model.objects.exclude(phone="").exclude(phone__isnull=True) \
                                .values_list("id", name_field, "phone")
            changes = [
                (pk, who, old, clean_phone(old))
                for pk, who, old in rows
                if clean_phone(old) != old
            ]
            if not changes:
                self.stdout.write(f"{label}: nothing to fix.")
                continue

            total += len(changes)
            self.stdout.write(self.style.WARNING(
                f"{label}: {len(changes)} phone number(s) to repair"))
            for pk, who, old, new in changes[:opts["limit"]]:
                self.stdout.write(f"   #{pk} {str(who)[:30]:30} {old!r} -> {new!r}")
            if len(changes) > opts["limit"]:
                self.stdout.write(f"   … and {len(changes) - opts['limit']} more")

            if apply_changes:
                # Bulk update: save() would fire the full model save path
                # (Client.save reallocates ids and stamps edited_at) on
                # thousands of rows for a two-character correction.
                with transaction.atomic():
                    for pk, _who, _old, new in changes:
                        model.objects.filter(pk=pk).update(phone=new)
                self.stdout.write(self.style.SUCCESS(f"{label}: {len(changes)} repaired."))

        if not total:
            self.stdout.write(self.style.SUCCESS("Nothing to do — all phone numbers are clean."))
        elif not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\nDry run: {total} row(s) would change. Re-run with --apply to write."))

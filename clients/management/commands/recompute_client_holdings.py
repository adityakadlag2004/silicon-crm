"""Re-derive every client's Portfolio columns from their approved sales.

`signals.update_client_status` only fires when a Sale is saved, so fixing what
it computes changes nothing for clients whose sales were booked long ago. This
walks the book once and re-runs the same function, which is the only way the
correction reaches existing rows.

Manual tool, not in CRONJOBS: run after changing what the signal derives.
Dry run by default; `--apply` writes. Idempotent — a second run reports zero
changes.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from clients.models import Client, Sale
from clients.signals import update_client_status

FIELDS = ["sip_amount", "lumsum_investment", "life_cover", "health_cover",
          "motor_insured_value", "pms_amount", "sip_status", "life_status",
          "health_status", "motor_status", "pms_status"]


class Command(BaseCommand):
    help = "Recompute Client portfolio columns from approved sales (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the recomputed values (default: dry run).")
        parser.add_argument("--limit", type=int, default=0,
                            help="Only process this many clients (for a quick look).")

    def handle(self, *args, **opts):
        apply_changes = opts["apply"]
        if apply_changes:
            self._run(opts)
            return
        # Dry run: do the real work inside a transaction and roll it back, so
        # there is one implementation and no half-written rows if this dies
        # mid-loop. Restoring fields by hand would be a genuine write to
        # production every time somebody asked "what would this change?".
        with transaction.atomic():
            self._run(opts)
            transaction.set_rollback(True)

    def _run(self, opts):
        # Only clients who have ever had a sale can change.
        qs = Client.objects.filter(sales__isnull=False).distinct().order_by("id")
        if opts["limit"]:
            qs = qs[:opts["limit"]]

        changed, examples = 0, []
        for client in qs:
            before = {f: getattr(client, f) for f in FIELDS}
            # Reuse the signal itself so there is exactly one implementation of
            # "what does this client hold" — a second copy here would drift.
            sale = Sale.objects.filter(client=client).first()
            if sale is None:
                continue
            update_client_status(Sale, sale)
            client.refresh_from_db()
            diff = {f: (before[f], getattr(client, f))
                    for f in FIELDS if before[f] != getattr(client, f)}
            if not diff:
                continue
            changed += 1
            if len(examples) < 10:
                examples.append((client.name, diff))

        for name, diff in examples:
            self.stdout.write("  %-28s %s" % (
                name[:28], ", ".join(f"{f}: {o} -> {n}" for f, (o, n) in diff.items())))
        verb = "updated" if opts["apply"] else "would change"
        self.stdout.write(self.style.SUCCESS(
            f"{changed} client(s) {verb}."
            + ("" if opts["apply"] else "  Re-run with --apply to write.")))

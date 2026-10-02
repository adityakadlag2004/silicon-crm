"""Move the pre-SPANCO "Wealth" targets off SIP and onto Lumpsum. One-off.

Migration 0119 carried the old pipeline's Wealth bucket onto the SIP product.
Most of those rows are monthly SIPs (₹5,000, ₹10,000), but some are Wealth
*targets* — ₹10 lakh, ₹1 crore — which no one invests monthly. Since a SIP is
now counted ×12 (`LeadInterest.annual_value`), leaving a ₹1 crore target on SIP
would value that lead at ₹12 crore and put it at the top of every list.

A row is moved when its note says it came from the old Wealth bucket and its
amount is at least `THRESHOLD`. The note is kept, so where the row came from
stays on record. A lead that already has a Lumpsum row is reported and left
alone rather than merged.

Dry run by default; `--apply` writes. Idempotent — a moved row is no longer SIP.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clients.models import LeadInterest, Product

THRESHOLD = 100000
LEGACY_NOTE = "Wealth (pre-SPANCO)"


class Command(BaseCommand):
    help = "Re-file legacy Wealth targets (≥ ₹1L) from SIP to Lumpsum. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        lumpsum = Product.objects.filter(code="LUMSUM").first()
        if lumpsum is None:
            raise CommandError("No product with code LUMSUM.")
        rows = list(LeadInterest.objects.filter(
            product__code="SIP", note__startswith=LEGACY_NOTE, amount__gte=THRESHOLD,
        ).select_related("lead"))
        taken = set(LeadInterest.objects.filter(
            product=lumpsum, lead_id__in=[r.lead_id for r in rows]).values_list("lead_id", flat=True))

        moved = skipped = 0
        with transaction.atomic():
            for row in rows:
                tag = f"lead #{row.lead_id} {row.lead.customer_name}: ₹{row.amount:,.0f}"
                if row.lead_id in taken:
                    self.stdout.write(f"  skip  {tag} — already has a Lumpsum row")
                    skipped += 1
                    continue
                self.stdout.write(f"  move  {tag}  SIP → Lumpsum")
                if opts["apply"]:
                    row.product = lumpsum
                    row.save(update_fields=["product"])
                moved += 1
        verb = "Moved" if opts["apply"] else "Would move"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {moved} row(s); {skipped} skipped."
            + ("" if opts["apply"] else " Dry run — re-run with --apply.")))

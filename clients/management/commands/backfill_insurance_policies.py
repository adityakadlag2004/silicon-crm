"""Create the missing tracker policy for approved Health/Life sales.

`sync_policy_from_sale` runs from the sale approval path, so a sale approved
before that code existed — or before sub-products rolled up — never got a
policy row. 84 of 94 approved insurance sales were in that state, which left
the client profile's Insurance tab empty for people who plainly hold policies.

Manual tool, not in CRONJOBS: run once after deploying, and again if the
matching rules change. Dry run by default, `--apply` writes. Idempotent —
`sync_policy_from_sale` is keyed on `source_sale`, so a second run is a no-op.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from clients.models import Sale
from clients.services import insurance_sync


class Command(BaseCommand):
    help = "Create tracker policies for approved Health/Life sales that have none."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the policies (default: dry run).")

    def handle(self, *args, **opts):
        if opts["apply"]:
            self._run(True)
            return
        # Same shape as recompute_client_holdings: do the real work and roll it
        # back, so "what would this create?" never writes.
        with transaction.atomic():
            self._run(False)
            transaction.set_rollback(True)

    def _run(self, apply_changes):
        sales = (
            Sale.objects.filter(status=Sale.STATUS_APPROVED, policy__isnull=True)
            .filter(
                Q(product_ref__code__in=["HEALTH_INS", "LIFE_INS"])
                | Q(product_ref__parent__code__in=["HEALTH_INS", "LIFE_INS"])
                | Q(product__iexact="Health Insurance")
                | Q(product__iexact="Life Insurance")
            )
            .select_related("client", "product_ref", "product_ref__parent", "employee")
            .order_by("id")
        )
        made, skipped, shown = 0, 0, 0
        for sale in sales:
            policy = insurance_sync.sync_policy_from_sale(sale)
            if policy is None:
                skipped += 1
                continue
            made += 1
            if shown < 10:
                shown += 1
                self.stdout.write("  %-14s %-24s %-7s cover %-12s ends %s" % (
                    policy.policy_number[:14], sale.client.name[:24],
                    policy.insurance_type, policy.sum_insured, policy.end_date))
        verb = "created" if apply_changes else "would create"
        self.stdout.write(self.style.SUCCESS(
            f"{made} polic(y/ies) {verb}; {skipped} sale(s) not trackable."
            + ("" if apply_changes else "  Re-run with --apply to write.")))

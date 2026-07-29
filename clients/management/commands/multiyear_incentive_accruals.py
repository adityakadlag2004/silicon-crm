"""Issue the later-year points of multiyear health policies.

A 2- or 3-year health policy is paid for up front, so only the first year is
business at sale time and only that year's points are issued then. Each later
year's points fall due on the policy's anniversary — there is no renewal to
collect and nothing for anyone to enter, because the premium is already in.
This job is what makes those points actually arrive.

Idempotent: one accrual row per (sale, policy year), so a re-run or a missed
day changes nothing and back-dated policies catch up on the next run.

Daily at 6:10 AM (CRONJOBS). The employee is notified when points land.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Notification, Sale
from clients.services import incentives as inc


class Command(BaseCommand):
    help = "Issue points for the 2nd/3rd year of multiyear health policies whose anniversary has arrived."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would be issued without writing anything.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        today = timezone.localdate()
        issued = 0
        total = 0

        sales = (Sale.objects.filter(status=Sale.STATUS_APPROVED, policy_years__gt=1)
                 .select_related("employee__user", "client", "product_ref"))
        for sale in sales:
            if not sale._is_health_product():
                continue
            for accrual in inc.issue_due_accruals(sale, on_date=today, dry_run=dry):
                issued += 1
                total += accrual.points
                self.stdout.write(
                    f"  {sale.client} — year {accrual.year_index} of {sale.policy_years}: "
                    f"{accrual.points} pts to {sale.employee.user.username}"
                )
                if not dry:
                    self._notify(accrual, sale)

        verb = "would issue" if dry else "issued"
        self.stdout.write(self.style.SUCCESS(
            f"Multiyear accruals: {verb} {issued} year(s), {total} points."
        ))

    def _notify(self, accrual, sale):
        """Tell the seller their points landed — nothing else signals it."""
        try:
            Notification.objects.create(
                recipient=sale.employee.user,
                title="Multiyear policy points credited",
                body=(f"Year {accrual.year_index} of {sale.client}'s "
                      f"{sale.policy_years}-year policy came due today — "
                      f"{accrual.points:,.0f} points credited."),
                related_sale=sale,
            )
        except Exception:
            # Points are the job; a notification hiccup must never lose them.
            pass

"""One-off: take the yearly ladder prize back off the sales that carry it.

The FY prize is settled once a year, by hand, and recorded on the Life Bonus
Status page — no one has ever received it through a sale. Until 2026-08-05 a
sale that crossed a rung had the whole rung written onto it, which both
overstated that month's bill and would have paid the prize twice over once the
cash was recorded.

Strips the prize and nothing else: the base each sale was booked with stays
exactly as it is. It deliberately does NOT re-save the rows — save() would
reprice them under today's structure and hand pre-restructure sales a 1.75%
base they were never booked with, which is inventing money, not correcting it.

Dry run by default; pass --apply to write. Idempotent — a second run finds
nothing to do.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import F

from clients.models import IncentiveRule, Sale
from clients.services import incentives as inc


class Command(BaseCommand):
    help = "Remove yearly-ladder prize money written onto sales (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the change. Without this it only reports.")

    def handle(self, *args, **options):
        rows = list(
            Sale.objects.filter(bonus_points__gt=0)
            .select_related("employee__user", "product_ref")
            .order_by("date")
        )
        # Only the yearly ladder. A monthly bonus rule pays as it goes and is
        # none of this command's business.
        targets = [s for s in rows
                   if (r := s._rule()) and r.slab_period == IncentiveRule.PERIOD_FY]

        if not targets:
            self.stdout.write("Nothing to do — no sale carries ladder prize money.")
            return

        affected = sorted({(s.employee_id, inc.fy_start_year(s.date)) for s in targets})
        total = sum((s.bonus_points for s in targets), Decimal("0"))

        self.stdout.write(f"{len(targets)} sale(s) carry ₹{total} of prize money:")
        for s in targets:
            who = s.employee.user.username
            self.stdout.write(
                f"  #{s.id} {who} {s.date} FY{inc.fy_start_year(s.date)} "
                f"points {s.points} → {s.points - s.bonus_points} (drops {s.bonus_points})"
            )

        self._ladders("BEFORE", affected)

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("\nDry run. Re-run with --apply to write."))
            return

        n = Sale.objects.filter(pk__in=[s.pk for s in targets]).update(
            points=F("points") - F("bonus_points"),
            incentive_amount=F("incentive_amount") - F("bonus_points"),
            bonus_points=Decimal("0"),
        )
        self.stdout.write(self.style.SUCCESS(f"\nCleared {n} sale(s)."))
        self._ladders("AFTER", affected)

    def _ladders(self, label, affected):
        from clients.models import Employee

        rule = IncentiveRule.objects.filter(
            active=True, slab_mode=IncentiveRule.MODE_BONUS,
            slab_period=IncentiveRule.PERIOD_FY).first()
        if rule is None:
            return
        self.stdout.write(f"\n=== Ladder {label} ===")
        for emp_id, fy in affected:
            e = Employee.objects.get(pk=emp_id)
            st = inc.life_bonus_status(rule, e, fy)
            self.stdout.write(
                f"  {e.user.username:16} FY{fy}  volume {st['volume']:>12}  "
                f"level {st['level']:>9}  handed over {st['released']:>9}  "
                f"owed {st['shortfall']:>9}  due now {st['due_now']:>9}  "
                f"payable {st['payable_from']}"
            )

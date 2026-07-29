"""Seed the agreed employee incentive structure for Life and Health insurance.

Life — 1.75% of every policy, plus a financial-year bonus ladder. The ladder
figure is what the seller has earned *to date* once their Apr–Mar cumulative
premium crosses the rung, so only the difference against what the year already
released is paid out:

    FY cumulative     prize earned to date
    3,00,000                     3,000
    9,00,000                     7,500
    18,00,000                   20,000
    30,00,000                   40,000
    45,00,000                   65,000
    60,00,000                   90,000
    75,00,000                  115,000
    90,00,000                  140,000

Health — a rate band picked from the seller's own monthly Fresh volume, set at
a flat 10% of the commission the firm earns in the matching band of its own
margin grid. Port sits outside the ladder at 0.67%.

    Monthly Fresh (per employee)   firm earns   seller gets
    < 25,000                          15.0%        1.50%
    25,000 - 50,000                   20.0%        2.00%
    50,000 - 1,00,000                 22.5%        2.25%
    1,00,000 - 2,00,000               27.5%        2.75%
    2,00,000 - 3,00,000               30.0%        3.00%
    >= 3,00,000                       35.0%        3.50%

Manual tool (not in CRONJOBS). Idempotent: re-running replaces both ladders.
Existing sales keep the points they were paid — like every other rule change,
this takes effect on sales saved from here on.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clients.models import IncentiveRule, IncentiveSlab, Product

# (FY cumulative premium, bonus earned to date)
LIFE_LADDER = [
    ("300000", "3000"),
    ("900000", "7500"),
    ("1800000", "20000"),
    ("3000000", "40000"),
    ("4500000", "65000"),
    # Above ₹45L the ladder used to stop, leaving a high performer on the base
    # alone. These keep it climbing so there is no ceiling to grow into.
    ("6000000", "90000"),
    ("7500000", "115000"),
    ("9000000", "140000"),
]
LIFE_BASE_PER_1000 = Decimal("17.500")   # 1.75%

# (monthly Fresh volume from, rate %)
HEALTH_BANDS = [
    ("0", "1.50"),
    ("25000", "2.00"),
    ("50000", "2.25"),
    ("100000", "2.75"),
    ("200000", "3.00"),
    ("300000", "3.50"),
]
HEALTH_PORT_PERCENT = Decimal("0.670")


class Command(BaseCommand):
    help = "Seed/refresh the Life FY bonus ladder and the Health monthly rate bands."

    def handle(self, *args, **opts):
        with transaction.atomic():
            life = self._rule("LIFE_INS", "Life Insurance")
            life.unit_amount = Decimal("1000")
            life.points_per_unit = LIFE_BASE_PER_1000
            life.slab_mode = IncentiveRule.MODE_BONUS
            life.slab_period = IncentiveRule.PERIOD_FY
            life.port_percent = None
            life.active = True
            life.save()
            life.slabs.all().delete()
            IncentiveSlab.objects.bulk_create([
                IncentiveSlab(rule=life, threshold=Decimal(t), payout=Decimal(p),
                              label=f"₹{int(Decimal(t)):,} FY")
                for t, p in LIFE_LADDER
            ])

            health = self._rule("HEALTH_INS", "Health Insurance")
            # Kept as the fallback rate if every band is ever deleted.
            health.unit_amount = Decimal("15000")
            health.points_per_unit = Decimal("200.000")
            health.slab_mode = IncentiveRule.MODE_RATE
            health.slab_period = IncentiveRule.PERIOD_MONTH
            health.port_percent = HEALTH_PORT_PERCENT
            health.active = True
            health.save()
            health.slabs.all().delete()
            IncentiveSlab.objects.bulk_create([
                IncentiveSlab(rule=health, threshold=Decimal(t), payout=Decimal(r),
                              label=f"{r}% band")
                for t, r in HEALTH_BANDS
            ])

        self.stdout.write(self.style.SUCCESS(
            f"Life: 1.75% base + {len(LIFE_LADDER)} FY rungs. "
            f"Health: {len(HEALTH_BANDS)} monthly rate bands, Port {HEALTH_PORT_PERCENT}%."
        ))

    def _rule(self, code, name):
        product = (Product.objects.filter(code=code).first()
                   or Product.objects.filter(name__iexact=name).first())
        if product is None:
            raise CommandError(f"No {name} product found (code {code}).")
        rule = (IncentiveRule.objects.filter(product_ref=product).first()
                or IncentiveRule.objects.filter(product=name).first())
        if rule is None:
            rule = IncentiveRule(product=name, product_ref=product,
                                 unit_amount=Decimal("1000"), points_per_unit=Decimal("0"))
        rule.product_ref = product
        return rule

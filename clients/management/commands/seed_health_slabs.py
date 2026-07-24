"""Seed the Health Insurance margin structure.

Fresh policies earn a slab rate driven by the month's cumulative Fresh business
(the report aggregates monthly Fresh revenue, then resolves the slab). Port
policies and all renewals are a flat 15%.

    Monthly Fresh business    Margin
    < 25,000                  15.0%
    25,000 – 50,000           20.0%
    50,000 – 1,00,000         22.5%
    1,00,000 – 2,00,000       27.5%
    2,00,000 – 3,00,000       30.0%
    > 3,00,000                35.0%

Port = 15% (the product's default margin, used when no Fresh slab applies),
renewals = 15% (renewal_margin_percent). Each band's boundary belongs to the
higher band (e.g. exactly 25,000 = 20%), and exactly 3,00,000 is still 30% —
only strictly above it is 35% — matching the stated "greater than 3 lakh".

Manual tool (not in CRONJOBS). Idempotent: re-running replaces the Fresh slabs.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clients.models import Product, ProductMarginSlab

# (min inclusive, max inclusive or None, margin %). Upper bounds sit one paisa
# below the next band so the boundary amount lands in the higher band.
FRESH_SLABS = [
    ("0", "24999.99", "15.00"),
    ("25000", "49999.99", "20.00"),
    ("50000", "99999.99", "22.50"),
    ("100000", "199999.99", "27.50"),
    ("200000", "300000", "30.00"),
    ("300000.01", None, "35.00"),
]


class Command(BaseCommand):
    help = "Seed/refresh the Health Insurance Fresh margin slabs + Port/renewal 15%."

    def handle(self, *args, **opts):
        product = (Product.objects.filter(code="HEALTH_INS").first()
                   or Product.objects.filter(name__iexact="Health Insurance").first())
        if product is None:
            raise CommandError("No Health Insurance product found (code HEALTH_INS).")

        with transaction.atomic():
            # Port + fallback margin and renewal margin are a flat 15%.
            product.margin_percent = Decimal("15.00")
            product.renewal_margin_percent = Decimal("15.00")
            product.save(update_fields=["margin_percent", "renewal_margin_percent", "updated_at"])

            # Replace only the Fresh slabs (leave any Port/other rows untouched).
            product.margin_slabs.filter(policy_type="fresh").delete()
            ProductMarginSlab.objects.bulk_create([
                ProductMarginSlab(
                    product=product, policy_type="fresh",
                    min_amount=Decimal(lo),
                    max_amount=None if hi is None else Decimal(hi),
                    margin_percent=Decimal(pct),
                )
                for lo, hi, pct in FRESH_SLABS
            ])

        self.stdout.write(self.style.SUCCESS(
            f"Health Insurance: {len(FRESH_SLABS)} Fresh slabs set; "
            f"Port + renewals = 15%."
        ))

"""Seed life-insurance plans + their PPT commission rates from the insurer chart.

Reads docs/insurance/life_rates.json (parsed from the Agency FYC-RYC PDF) and
creates each offline plan as a sub-product under "Life Insurance", along with its
per-PPT Advisor + MDRT rate rows. Idempotent: matches plans by code and replaces
their rate rows, so re-running after a chart update (a new PDF version) refreshes
the numbers without duplicating products. New plans start INACTIVE — an admin
ticks the ones actually sold on the Product Management page.

Manual tool (not in CRONJOBS): run once after deploy, and again when the insurer
publishes a new commission chart version.
"""

import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from clients.models import PlanPptRate, Product

DATA = Path(settings.BASE_DIR) / "docs" / "insurance" / "life_rates.json"


def _dec(v):
    return None if v is None else Decimal(str(v))


class Command(BaseCommand):
    help = "Seed/refresh life-insurance plans and their PPT commission rates."

    def handle(self, *args, **opts):
        plans = json.loads(DATA.read_text())

        with transaction.atomic():
            parent, _ = Product.objects.get_or_create(
                code="LIFE_INS",
                defaults={"name": "Life Insurance", "domain": Product.DOMAIN_BOTH,
                          "is_active": True},
            )

            created, refreshed, rows = 0, 0, 0
            for p in plans:
                product, is_new = Product.objects.get_or_create(
                    code=p["code"],
                    defaults={
                        "name": p["name"][:100],
                        "parent": parent,
                        "domain": Product.DOMAIN_BOTH,
                        "is_active": False,  # admin ticks what's actually sold
                    },
                )
                if is_new:
                    created += 1
                else:
                    refreshed += 1
                    product.ppt_rates.all().delete()  # replace rates on refresh

                PlanPptRate.objects.bulk_create([
                    PlanPptRate(
                        product=product, designation=r["desig"], ppt=r["ppt"],
                        fyc=_dec(r["fyc"]), ryc_2nd=_dec(r["ryc2"]),
                        ryc_3rd=_dec(r["ryc3"]), ryc_4th=_dec(r["ryc4"]),
                        ryc_5plus=_dec(r["ryc5"]),
                    )
                    for r in p["rates"]
                ])
                rows += len(p["rates"])

        self.stdout.write(self.style.SUCCESS(
            f"Life rates seeded: {created} new plan(s), {refreshed} refreshed, "
            f"{rows} rate rows. New plans are inactive — tick the ones you sell."
        ))

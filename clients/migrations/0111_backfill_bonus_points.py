"""Backfill `Sale.bonus_points` for sales paid under the old slab-only rules.

Before this release a rule with slabs ignored its unit rate entirely, so the
whole of `points` on those sales *was* the slab delta. The earned-to-date
ladder now measures against `bonus_points`; without this backfill every
historical slab rung would look unpaid and release itself a second time on the
next recompute.

Campaign-paid sales are left alone — campaign slabs still track their own delta
through `points`.
"""

from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    IncentiveRule = apps.get_model("clients", "IncentiveRule")
    Sale = apps.get_model("clients", "Sale")

    for rule in IncentiveRule.objects.filter(slabs__isnull=False).distinct():
        qs = Sale.objects.filter(campaign__isnull=True)
        if rule.product_ref_id:
            qs = qs.filter(product_ref_id=rule.product_ref_id)
        else:
            qs = qs.filter(product=rule.product)
        qs.update(bonus_points=F("points"))


def backwards(apps, schema_editor):
    apps.get_model("clients", "Sale").objects.update(bonus_points=0)


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0110_incentiverule_port_percent_incentiverule_slab_mode_and_more"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]

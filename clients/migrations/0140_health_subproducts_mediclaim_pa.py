"""Health Insurance gets two sub-products: Mediclaim and Personal Accident.

PA is sold on Health's terms (1–5 year term, EMI) and rolls up into Health in
every report. Once a product has sub-products the sale forms require one, so
ordinary health sales need Mediclaim to land on — PA alone would make every
health sale a PA sale. Both start on Health's own margin and renewal rates (a
renewal is valued at its plan's rate, so 0% would zero Health's renewal
margin); PA's rates are adjusted on Product Management.
"""

from django.db import migrations

SUBPRODUCTS = [("Mediclaim", "HEALTH_MEDICLAIM"), ("Personal Accident", "HEALTH_PA")]


def add(apps, schema_editor):
    Product = apps.get_model("clients", "Product")
    health = Product.objects.filter(code="HEALTH_INS").first()
    if health is None:
        return
    for order, (name, code) in enumerate(SUBPRODUCTS, start=1):
        if Product.objects.filter(code=code).exists() or Product.objects.filter(name__iexact=name).exists():
            continue
        Product.objects.create(
            name=name, code=code, parent=health, domain=health.domain,
            display_order=order, margin_percent=health.margin_percent,
            renewal_margin_percent=health.renewal_margin_percent,
            is_active=True, show_in_reports=health.show_in_reports,
        )


def remove(apps, schema_editor):
    Product = apps.get_model("clients", "Product")
    for _name, code in SUBPRODUCTS:
        p = Product.objects.filter(code=code).first()
        # Sales/renewals would be orphaned (SET_NULL) — keep a plan once used.
        if p and not p.sales.exists() and not p.renewal_entries.exists():
            p.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0139_sale_emi_17_23_months"),
    ]

    operations = [migrations.RunPython(add, remove)]

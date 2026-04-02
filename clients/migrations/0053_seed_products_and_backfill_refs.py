from django.db import migrations


def seed_and_backfill(apps, schema_editor):
    Product = apps.get_model("clients", "Product")
    Sale = apps.get_model("clients", "Sale")
    IncentiveRule = apps.get_model("clients", "IncentiveRule")
    Target = apps.get_model("clients", "Target")
    Redemption = apps.get_model("clients", "Redemption")
    MonthlyTargetHistory = apps.get_model("clients", "MonthlyTargetHistory")
    Renewal = apps.get_model("clients", "Renewal")

    seed = [
        ("SIP", "SIP", "sale", 10),
        ("Lumsum", "LUMSUM", "sale", 20),
        ("Life Insurance", "LIFE_INS", "both", 30),
        ("Health Insurance", "HEALTH_INS", "both", 40),
        ("Motor Insurance", "MOTOR_INS", "sale", 50),
        ("PMS", "PMS", "sale", 60),
        ("COB", "COB", "sale", 70),
        ("Other", "OTHER", "renewal", 999),
    ]

    product_by_name = {}
    for name, code, domain, display_order in seed:
        obj, _ = Product.objects.get_or_create(
            name=name,
            defaults={"code": code, "domain": domain, "display_order": display_order, "is_active": True},
        )
        if obj.code != code or obj.domain != domain:
            obj.code = code
            obj.domain = domain
            obj.display_order = display_order
            obj.save(update_fields=["code", "domain", "display_order", "updated_at"])
        product_by_name[name] = obj

    def get_or_create_product(name, domain="sale"):
        cleaned = (name or "").strip()
        if not cleaned:
            return None
        found = Product.objects.filter(name=cleaned).first()
        if found:
            return found
        code = cleaned.upper().replace(" ", "_")[:30]
        obj, _ = Product.objects.get_or_create(
            name=cleaned,
            defaults={"code": code, "domain": domain, "is_active": True},
        )
        return obj

    for sale in Sale.objects.all().only("id", "product"):
        p = get_or_create_product(sale.product, domain="sale")
        sale.product_ref_id = p.id if p else None
        sale.product_name_snapshot = sale.product or ""
        sale.save(update_fields=["product_ref", "product_name_snapshot"])

    for rule in IncentiveRule.objects.all().only("id", "product"):
        p = get_or_create_product(rule.product, domain="sale")
        rule.product_ref_id = p.id if p else None
        rule.save(update_fields=["product_ref"])

    for target in Target.objects.all().only("id", "product"):
        p = get_or_create_product(target.product, domain="sale")
        target.product_ref_id = p.id if p else None
        target.save(update_fields=["product_ref"])

    for redemption in Redemption.objects.all().only("id", "product"):
        p = get_or_create_product(redemption.product, domain="sale")
        redemption.product_ref_id = p.id if p else None
        redemption.save(update_fields=["product_ref"])

    for history in MonthlyTargetHistory.objects.all().only("id", "product"):
        p = get_or_create_product(history.product, domain="sale")
        history.product_ref_id = p.id if p else None
        history.save(update_fields=["product_ref"])

    for renewal in Renewal.objects.all().only("id", "product_type"):
        if renewal.product_type == "life_insurance":
            p = product_by_name.get("Life Insurance")
        elif renewal.product_type == "health_insurance":
            p = product_by_name.get("Health Insurance")
        else:
            p = product_by_name.get("Other")
        renewal.product_ref_id = p.id if p else None
        renewal.save(update_fields=["product_ref"])


def reverse_seed_and_backfill(apps, schema_editor):
    Sale = apps.get_model("clients", "Sale")
    IncentiveRule = apps.get_model("clients", "IncentiveRule")
    Target = apps.get_model("clients", "Target")
    Redemption = apps.get_model("clients", "Redemption")
    MonthlyTargetHistory = apps.get_model("clients", "MonthlyTargetHistory")
    Renewal = apps.get_model("clients", "Renewal")

    Sale.objects.update(product_ref=None)
    IncentiveRule.objects.update(product_ref=None)
    Target.objects.update(product_ref=None)
    Redemption.objects.update(product_ref=None)
    MonthlyTargetHistory.objects.update(product_ref=None)
    Renewal.objects.update(product_ref=None)


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0052_product_sale_product_name_snapshot_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_and_backfill, reverse_seed_and_backfill),
    ]

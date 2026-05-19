from django.db import migrations


DEFAULT_CATEGORIES = ["Electricity", "Material", "Marketing"]


def seed_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("clients", "ExpenseCategory")
    for order, name in enumerate(DEFAULT_CATEGORIES):
        ExpenseCategory.objects.get_or_create(
            name=name, defaults={"display_order": order, "is_active": True}
        )


def unseed_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("clients", "ExpenseCategory")
    ExpenseCategory.objects.filter(name__in=DEFAULT_CATEGORIES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0064_expensecategory_expense"),
    ]

    operations = [
        migrations.RunPython(seed_categories, unseed_categories),
    ]

from django.db import migrations
from django.utils import timezone


def approve_existing_sales(apps, schema_editor):
    Sale = apps.get_model("clients", "Sale")
    Sale.objects.filter(status="pending").update(status="approved", approved_at=timezone.now())


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0041_fix_sale_approval_columns"),
    ]

    operations = [
        migrations.RunPython(approve_existing_sales, noop),
    ]

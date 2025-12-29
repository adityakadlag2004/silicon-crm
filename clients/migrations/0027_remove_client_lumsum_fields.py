from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0026_alter_client_lumsum_defaults"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="client",
            name="lumsum_amount",
        ),
        migrations.RemoveField(
            model_name="client",
            name="lumsum_status",
        ),
    ]

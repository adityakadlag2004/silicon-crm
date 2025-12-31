from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0027_remove_client_lumsum_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="lumsum_investment",
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=12, null=True),
        ),
    ]

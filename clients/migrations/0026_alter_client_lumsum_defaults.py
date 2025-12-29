from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0025_client_lumsum_edited_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='client',
            name='lumsum_amount',
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=12, null=True),
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0024_alter_businesstarget_id_alter_notification_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='lumsum_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='client',
            name='lumsum_status',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='client',
            name='edited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='client',
            name='edited_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='edited_clients', to='clients.employee'),
        ),
    ]

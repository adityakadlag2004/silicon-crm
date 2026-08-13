"""Schema for the SPANCO lead pipeline. The data pass is 0119.

Split deliberately: `stage_changed_at` is indexed, and Django defers CREATE
INDEX to the end of a migration's transaction — Postgres then refuses it
("pending trigger events") because the data pass has just updated every lead
row in that same transaction.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0117_remove_incentiverule_port_percent'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='lost_reason',
            field=models.CharField(blank=True, help_text='Why the lead was dropped — this is what makes weak stages visible.', max_length=255),
        ),
        migrations.AddField(
            model_name='lead',
            name='stage_changed_at',
            field=models.DateTimeField(db_index=True, default=django.utils.timezone.now),
        ),
        migrations.AlterField(
            model_name='lead',
            name='stage',
            field=models.CharField(choices=[('suspect', 'Suspect'), ('prospect', 'Prospect'), ('approach', 'Approach / Analysis'), ('negotiation', 'Negotiation'), ('conclusion', 'Conclusion'), ('order', 'Order')], db_index=True, default='suspect', max_length=20),
        ),
        migrations.CreateModel(
            name='LeadInterest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(blank=True, decimal_places=2, help_text='Indicative premium / SIP / cover being discussed. Optional.', max_digits=14, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ('note', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='interests', to='clients.lead')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='lead_interests', to='clients.product')),
            ],
            options={
                'ordering': ['product__display_order', 'id'],
                'unique_together': {('lead', 'product')},
            },
        ),
        migrations.CreateModel(
            name='LeadStageEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('from_stage', models.CharField(blank=True, max_length=20)),
                ('to_stage', models.CharField(max_length=20)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stage_events', to='clients.lead')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]

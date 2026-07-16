# Lead Records (lead sheets) module removed — drop the four tables.
# Hand-written: the auto-generated version removed fields before the
# unique_together that referenced them, which fails on table creation.
# DeleteModel in dependency order (children first) is all that's needed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0088_product_rta_match'),
    ]

    operations = [
        migrations.DeleteModel(
            name='LeadSheetFollowUp',
        ),
        migrations.DeleteModel(
            name='LeadSheetColumn',
        ),
        migrations.DeleteModel(
            name='LeadSheetRecord',
        ),
        migrations.DeleteModel(
            name='LeadSheet',
        ),
    ]

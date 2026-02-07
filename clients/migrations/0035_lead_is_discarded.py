from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0034_alter_lead_id_alter_leadfamilymember_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="is_discarded",
            field=models.BooleanField(default=False),
        ),
    ]

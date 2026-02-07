from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0035_lead_is_discarded"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="employee_number",
            field=models.CharField(blank=True, max_length=50, null=True, unique=True),
        ),
    ]

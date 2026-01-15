from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0031_netsipentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="active",
            field=models.BooleanField(default=True),
        ),
    ]

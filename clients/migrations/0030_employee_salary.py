from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0029_netbusinessentry"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="salary",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]

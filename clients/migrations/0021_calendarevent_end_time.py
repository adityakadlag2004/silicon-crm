from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0020_messagelog'),
    ]

    operations = [
        migrations.AddField(
            model_name='calendarevent',
            name='end_time',
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]

# Generated manually to restore missing dependency for BusinessTarget
from django.db import migrations, models
from django.conf import settings

class Migration(migrations.Migration):

    dependencies = [
        ('clients', '0022_notification'),
    ]

    operations = [
        migrations.CreateModel(
            name='BusinessTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('metric', models.CharField(max_length=100)),
                ('target_value', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('unit', models.CharField(blank=True, default='', max_length=50)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('active', models.BooleanField(default=True)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-start_date', '-end_date', '-created_at'],
            },
        ),
    ]

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0036_employee_employee_number"),
    ]

    operations = [
        migrations.CreateModel(
            name="LeadRemark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("lead", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="remarks", to="clients.lead")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LeadFollowUp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scheduled_time", models.DateTimeField()),
                ("note", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("done", "Done")], default="pending", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assigned_to", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lead_followups", to="clients.employee")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("lead", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="followups", to="clients.lead")),
            ],
            options={"ordering": ["scheduled_time"]},
        ),
    ]

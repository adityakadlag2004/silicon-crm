from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0032_employee_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Lead",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_name", models.CharField(max_length=255)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("data_received", models.BooleanField(default=False)),
                ("data_received_on", models.DateField(blank=True, null=True)),
                ("income", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("expenses", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "stage",
                    models.CharField(
                        choices=[("pending", "Pending"), ("half_sold", "Half Sold"), ("processed", "Processed")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assigned_to",
                    models.ForeignKey(
                        help_text="Employee responsible for this lead",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="leads",
                        to="clients.employee",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_leads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="LeadFamilyMember",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("relation", models.CharField(blank=True, max_length=100)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "lead",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="family_members",
                        to="clients.lead",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="LeadProductProgress",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "product",
                    models.CharField(
                        choices=[("health", "Health"), ("life", "Life"), ("wealth", "Wealth")], max_length=20
                    ),
                ),
                (
                    "target_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
                (
                    "achieved_amount",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=14,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("half_sold", "Half Sold"), ("processed", "Processed")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("remark", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "lead",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="progress_entries",
                        to="clients.lead",
                    ),
                ),
            ],
            options={
                "ordering": ["product"],
                "unique_together": {("lead", "product")},
            },
        ),
    ]

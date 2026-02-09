from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0040_mark_existing_sales_approved"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManagerAccessConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allow_view_all_sales", models.BooleanField(default=True)),
                ("allow_approve_sales", models.BooleanField(default=False)),
                ("allow_edit_sales", models.BooleanField(default=False)),
                ("allow_manage_incentives", models.BooleanField(default=False)),
                ("allow_recalc_points", models.BooleanField(default=False)),
                ("allow_client_analysis", models.BooleanField(default=False)),
                ("allow_employee_performance", models.BooleanField(default=True)),
                ("allow_lead_management", models.BooleanField(default=False)),
                ("allow_calling_admin", models.BooleanField(default=False)),
                ("allow_business_tracking", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]

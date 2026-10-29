"""Everyone who works here today works at the head office.

The second office is added on the Django admin and its people are moved on the
team edit page. Kept apart from 0141: a deferred FK index cannot follow a bulk
row update in one Postgres transaction.
"""

from django.db import migrations


def forward(apps, schema_editor):
    Branch = apps.get_model("clients", "Branch")
    Employee = apps.get_model("clients", "Employee")
    head, _ = Branch.objects.get_or_create(name="Head Office")
    Employee.objects.filter(branch__isnull=True).update(branch=head)


class Migration(migrations.Migration):

    dependencies = [("clients", "0141_employee_branch")]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]

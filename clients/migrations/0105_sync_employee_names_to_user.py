"""Backfill User.first_name/last_name from Employee for existing rows.

New saves keep them in sync (Employee.save); this catches everyone who
filled their profile before that existed and was still greeted by login id.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Employee = apps.get_model("clients", "Employee")
    User = apps.get_model("auth", "User")
    for emp in Employee.objects.exclude(first_name="", last_name="").select_related("user"):
        changed = {f: getattr(emp, f) for f in ("first_name", "last_name")
                   if getattr(emp, f) and getattr(emp, f) != getattr(emp.user, f)}
        if changed:
            User.objects.filter(pk=emp.user_id).update(**changed)


class Migration(migrations.Migration):

    dependencies = [("clients", "0104_remove_employee_blood_group_and_more")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

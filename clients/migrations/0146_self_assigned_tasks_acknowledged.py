"""Tasks people gave themselves count as acknowledged.

Task.save() does this for every new row from here on; this catches the rows
already in the table, which are still being re-rung every four hours ("Task
awaiting your acknowledgement") at the person who wrote them. Stamped with
the task's own creation time — that is when its author saw it.
"""
from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    Task = apps.get_model("clients", "Task")
    Task.objects.filter(
        acknowledged_at__isnull=True, assigned_to__user_id=F("created_by_id"),
    ).update(acknowledged_at=F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0145_recurring_rule_assignees"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

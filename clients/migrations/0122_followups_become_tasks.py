"""Turn every lead/claim follow-up row into the Task it now is.

Kept separate from 0121 on purpose: that one builds the `task_source_idx`
index, and a deferred CREATE INDEX must not share a transaction with a bulk
row rewrite in Postgres.

Nothing is thrown away — a done follow-up becomes a completed task, so the
record of what was chased and when survives the model being dropped in 0123.
"""
from django.db import migrations
from django.utils import timezone

SYSTEM_USERNAME = "system"
CATEGORY_NAME = "Follow-up"
# What set_unusable_password() writes. The historical User model has no
# methods, so the marker goes in by hand.
UNUSABLE_PASSWORD = "!"


def _due(scheduled):
    local = timezone.localtime(scheduled)
    return local.date(), local.time().replace(second=0, microsecond=0)


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Task = apps.get_model("clients", "Task")
    TaskCategory = apps.get_model("clients", "TaskCategory")
    LeadFollowUp = apps.get_model("clients", "LeadFollowUp")
    ClaimReminder = apps.get_model("clients", "ClaimReminder")

    system, _ = User.objects.get_or_create(
        username=SYSTEM_USERNAME,
        defaults={"first_name": "System", "is_active": False,
                  "password": UNUSABLE_PASSWORD},
    )
    category, _ = TaskCategory.objects.get_or_create(
        name=CATEGORY_NAME,
        defaults={"color": "#0EA5E9", "icon": "bi-clock-history",
                  "description": "Auto-generated follow-up on a lead, claim or client."},
    )

    rows = []
    for fu in LeadFollowUp.objects.select_related("lead").all():
        due_date, due_time = _due(fu.scheduled_time)
        done = fu.status == "done"
        lines = [fu.note] if fu.note else []
        lines.append(f"SPANCO stage: {fu.lead.stage.replace('_', ' ').title()}")
        if fu.lead.phone:
            lines.append(f"Phone: {fu.lead.phone}")
        lines.append(f"/clients/leads/{fu.lead_id}/")
        rows.append(Task(
            title=f"SPANCO Lead follow-up — {fu.lead.customer_name}"[:255],
            description="\n".join(lines),
            category=category,
            priority="medium",
            status="completed" if done else "pending",
            created_by=system,
            assigned_to_id=fu.assigned_to_id,
            client_id=fu.lead.converted_client_id,
            due_date=due_date,
            due_time=due_time,
            completed_at=fu.scheduled_time if done else None,
            source_kind="lead",
            source_id=fu.lead_id,
        ))

    for rem in ClaimReminder.objects.select_related("claim__policy__client").all():
        due_date, due_time = _due(rem.scheduled_at)
        done = rem.status in ("done", "dismissed")
        policy = rem.claim.policy
        lines = [rem.note] if rem.note else []
        lines += [f"Claim on policy {policy.policy_number or '(no number)'}",
                  f"Stage: {rem.claim.status.replace('_', ' ').title()}"]
        lines.append(f"/clients/claims/{rem.claim_id}/")
        rows.append(Task(
            title=f"Claim follow-up — {policy.client.name}"[:255],
            description="\n".join(lines),
            category=category,
            priority="medium",
            status="completed" if done else "pending",
            created_by=system,
            assigned_to_id=rem.employee_id,
            client_id=policy.client_id,
            due_date=due_date,
            due_time=due_time,
            completed_at=rem.completed_at,
            source_kind="claim",
            source_id=rem.claim_id,
        ))

    Task.objects.bulk_create(rows, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0121_task_followup_source"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

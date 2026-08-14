"""Follow-ups are Tasks. There is no other kind of follow-up.

Every dated follow-up in the CRM — a SPANCO lead call, a claim chase — is a
`Task` and nothing else. It used to be one small model per module
(`LeadFollowUp`, `ClaimReminder`), each with its own list, its own "done"
button and its own reminder, and the reminder was the weak part: a lead
follow-up got a plain tray notification while a task rings the phone like an
alarm clock. Folding them into Task buys the whole task pipeline for free —
`tasks_ring_due` rings the exact due minute, the Android app arms the same
alarm locally so it fires offline, `tasks_mark_overdue` chases what slipped,
and it shows on the common calendar as a task.

Two rows for one commitment is the thing to avoid: complete the task, leave
the follow-up pending, and the calendar still nags about work that is done.
So the follow-up row is gone and the task IS the record.

`Task.source_kind` / `source_id` point back at what the follow-up was raised
on, so a lead page can list its own follow-ups and closing that lead can
cancel them.

Who is who on a system follow-up:

  created_by   the **system** user — that is what marks a task as generated
               rather than typed by a person, and it is why the human who
               scheduled it still gets the "assigned" ring when it lands on
               someone else.
  assigned_to  whoever OWNS the record (the lead's employee, the claim's
               handler), falling back to whoever scheduled it. An admin
               scheduling a follow-up on someone else's lead must ring that
               employee's phone, not their own.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from ..models import Task, TaskActivity, TaskCategory
from .tasks import log_activity, notify_task

SYSTEM_USERNAME = "system"
CATEGORY_NAME = "Follow-up"

# Known sources. A new one needs a branch in _describe() and _owner(); there
# are two, so a dict of adapters would be more machinery than the thing itself.
LEAD = "lead"
CLAIM = "claim"


def system_user():
    """The bot that owns generated tasks. Login is impossible by construction."""
    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        username=SYSTEM_USERNAME,
        defaults={"first_name": "System", "is_active": False},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def category():
    """The "Follow-up" task category, so the real task list stays readable."""
    cat, _ = TaskCategory.objects.get_or_create(
        name=CATEGORY_NAME,
        defaults={"color": "#0EA5E9", "icon": "bi-clock-history",
                  "description": "Auto-generated follow-up on a lead, claim or client."},
    )
    return cat


def _owner(kind, obj):
    if kind == LEAD:
        return obj.assigned_to
    if kind == CLAIM:
        return obj.handled_by
    return None


def _describe(kind, obj, note):
    """(title, description, client) for the task that carries this follow-up.

    The note leads: it is what the follow-up is actually about, and it is the
    line every narrow surface (a claim's rail, the calendar tile, the phone's
    task card) shows first when it can only show one. The context lines and
    the path back to the record follow it.
    """
    if kind == LEAD:
        lines = [note] if note else []
        lines.append(f"SPANCO stage: {obj.get_stage_display()}")
        if obj.phone:
            lines.append(f"Phone: {obj.phone}")
        lines.append(reverse("clients:lead_detail", args=[obj.pk]))
        return (f"SPANCO Lead follow-up — {obj.customer_name}",
                "\n".join(lines), obj.converted_client)

    if kind == CLAIM:
        client = obj.policy.client
        lines = [note] if note else []
        lines += [f"Claim on policy {obj.policy.policy_number or '(no number)'}",
                  f"Stage: {obj.get_status_display()}"]
        lines.append(reverse("clients:claim_detail", args=[obj.pk]))
        return f"Claim follow-up — {client.name}", "\n".join(lines), client

    raise ValueError(f"Unknown follow-up source: {kind!r}")


def schedule(kind, obj, when, *, note="", actor=None, owner=None,
             priority=Task.PRIORITY_MEDIUM):
    """Raise a follow-up on `obj`, due at `when` (an aware datetime).

    Priority defaults to medium on purpose: high/critical tasks re-ring every
    four hours until acknowledged, and twenty follow-ups a day at that volume
    is a notification storm people learn to swipe away.
    """
    local = timezone.localtime(when)
    title, description, client = _describe(kind, obj, (note or "").strip())
    assignee = owner or _owner(kind, obj) or getattr(actor, "employee", None)

    task = Task.objects.create(
        title=title[:255],
        description=description,
        category=category(),
        priority=priority,
        created_by=system_user(),
        assigned_to=assignee,
        client=client,
        due_date=local.date(),
        due_time=local.time().replace(second=0, microsecond=0),
        source_kind=kind,
        source_id=obj.pk,
    )
    by = getattr(actor, "username", None) or "the system"
    log_activity(task, system_user(), TaskActivity.CREATED,
                 f"Follow-up scheduled by {by} for {local:%d %b %Y %H:%M}.")
    # The actor is excluded from the fan-out, so scheduling your own follow-up
    # doesn't ping you — only handing one to someone else does.
    notify_task(task, actor, "Follow-up assigned",
                f"{task.title} — due {local:%d %b, %H:%M}.", event="assigned")
    return task


def for_source(kind, obj_id):
    """Every follow-up raised on one record, newest deadline last."""
    return (Task.objects.filter(source_kind=kind, source_id=obj_id, is_deleted=False)
            .select_related("assigned_to__user", "category")
            .order_by("-due_date", "-due_time"))


def open_for_source(kind, obj_id):
    return for_source(kind, obj_id).filter(status__in=Task.OPEN_STATUSES)


def cancel_open(kind, obj_id, actor=None, reason=""):
    """Close out the open follow-ups on a record that no longer needs chasing.

    A lead that reached Order or died is done being chased; leaving its
    follow-ups open means a phone rings for work nobody will do.
    """
    rows = list(open_for_source(kind, obj_id))
    for task in rows:
        task.status = Task.STATUS_CANCELLED
        task.save(update_fields=["status", "updated_at"])
        log_activity(task, actor, TaskActivity.STATUS_CHANGED,
                     reason or "Cancelled — the record it followed up on is closed.")
    return len(rows)

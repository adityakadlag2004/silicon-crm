"""Shared helpers for the Task Management module.

Two responsibilities used by every mutating task view:

  * ``log_activity`` — append an immutable TaskActivity row (audit trail).
  * ``notify_task``  — create in-app Notifications for the people watching a
    task (assignee + subscribers, minus the actor). Creating a Notification
    auto-mirrors it to FCM via ``signals.push_on_notification``, so we do not
    call the push service directly here.

Keeping this out of the views keeps each action a one-liner and guarantees the
audit trail and notifications stay consistent.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone

from ..models import NotificationPreference, Notification, TaskActivity


def log_activity(task, actor, action, detail=""):
    """Record one audit-trail entry for `task`. Returns the TaskActivity."""
    return TaskActivity.objects.create(
        task=task, actor=actor, action=action, detail=detail[:500],
    )


def user_wants(user, event):
    """Whether `user` wants a notification for `event` (default: yes).

    `event` is one of the NotificationPreference.notify_* suffixes, e.g.
    "assigned", "status_changed". A missing preference row means all-on.
    """
    if not user or not event:
        return True
    pref = NotificationPreference.objects.filter(user=user).first()
    if not pref:
        return True
    return bool(getattr(pref, f"notify_{event}", True))


def create_notification(user, title, body, link="", event=None):
    """Create a Notification for `user` unless their prefs mute `event`.

    Returns the Notification, or None when muted / no user. Creating a
    Notification auto-mirrors an FCM push via signals.push_on_notification.
    """
    if not user or not user_wants(user, event):
        return None
    return Notification.objects.create(recipient=user, title=title, body=body, link=link)


def task_recipients(task, exclude_user=None):
    """Users who should hear about a change: assignee + subscribers.

    Excludes `exclude_user` (usually the actor — no self-notifications) and
    de-duplicates by user id.
    """
    users = {}
    if task.assigned_to and task.assigned_to.user_id:
        users[task.assigned_to.user_id] = task.assigned_to.user
    for sub in task.subscribers.select_related("user"):
        if sub.user_id:
            users[sub.user_id] = sub.user
    if exclude_user is not None:
        users.pop(getattr(exclude_user, "id", None), None)
    return list(users.values())


def _task_link(task):
    try:
        return reverse("clients:task_detail", args=[task.pk])
    except Exception:
        return ""


def notify_task(task, actor, title, body, event=None):
    """Fan out an in-app (+push) notification to everyone watching `task`.

    `actor` is excluded so the person who made the change is not pinged. Each
    recipient's NotificationPreference for `event` is respected. Deep links to
    the task detail page (the Android app routes /clients/tasks/… ).
    """
    link = _task_link(task)
    recipients = task_recipients(task, exclude_user=actor)
    sent = 0
    for user in recipients:
        if create_notification(user, title, body, link, event):
            sent += 1
    return sent


# ─────────────────────────── recurrence engine ───────────────────────────

def _add_months(d, n):
    """Return `d` advanced by `n` months, clamping the day to month length."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_due_date(rule, after):
    """The next due date strictly after `after` for a RecurringTaskRule."""
    from ..models import RecurringTaskRule

    interval = rule.interval or 1
    if rule.frequency == RecurringTaskRule.FREQ_DAILY:
        return after + timedelta(days=interval)
    if rule.frequency == RecurringTaskRule.FREQ_WEEKLY:
        weekdays = rule.weekday_list()
        if weekdays:
            for i in range(1, 8):
                cand = after + timedelta(days=i)
                if cand.weekday() in weekdays:
                    return cand
            return after + timedelta(days=7)
        return after + timedelta(days=7 * interval)
    if rule.frequency == RecurringTaskRule.FREQ_MONTHLY:
        return _add_months(after, interval)
    # custom → treat like daily-with-interval
    return after + timedelta(days=interval)


def generate_rule_instances(rule, today=None, cap=60):
    """Create any Task instances for `rule` whose due date has arrived.

    Catches up on missed periods (bounded by `cap`), honours end conditions,
    copies the checklist + subscribers, logs activity, and notifies. Returns
    the list of created Task instances.
    """
    from ..models import RecurringTaskRule, Task, TaskChecklistItem, TaskSubscriber

    today = today or timezone.localdate()
    created = []
    if not rule.is_active:
        return created

    guard = 0
    while guard < cap:
        guard += 1

        if (rule.end_type == RecurringTaskRule.END_AFTER and rule.max_occurrences
                and rule.occurrences_created >= rule.max_occurrences):
            rule.is_active = False
            rule.save(update_fields=["is_active"])
            break

        anchor = rule.last_generated_date or rule.start_date
        nxt = next_due_date(rule, anchor)
        if not nxt or nxt > today:
            break
        if (rule.end_type == RecurringTaskRule.END_ON and rule.end_date
                and nxt > rule.end_date):
            rule.is_active = False
            rule.save(update_fields=["is_active"])
            break

        task = Task.objects.create(
            title=rule.title,
            description=rule.description,
            category=rule.category,
            priority=rule.priority,
            created_by=rule.created_by,
            assigned_to=rule.assigned_to,
            due_date=nxt,
            due_time=rule.due_time,
            repeat_rule=rule.frequency,
            recurring_rule=rule,
        )
        for i, line in enumerate(l for l in rule.checklist_template.splitlines() if l.strip()):
            TaskChecklistItem.objects.create(task=task, title=line.strip()[:255], order=i)
        for uid in rule.subscriber_id_list():
            TaskSubscriber.objects.get_or_create(task=task, user_id=uid)

        log_activity(task, rule.created_by, TaskActivity.CREATED,
                     f"Auto-generated from a {rule.get_frequency_display().lower()} recurring rule.")
        notify_task(task, None, "Recurring task created",
                    f"“{task.title}” is due {nxt:%d %b}.", event="recurring_created")

        rule.occurrences_created += 1
        rule.last_generated_date = nxt
        rule.save(update_fields=["occurrences_created", "last_generated_date"])
        created.append(task)

    return created


def generate_recurring(today=None):
    """Generate due instances for every active rule. Returns total created."""
    from ..models import RecurringTaskRule

    total = 0
    for rule in RecurringTaskRule.objects.filter(is_active=True):
        total += len(generate_rule_instances(rule, today=today))
    return total


def build_recurrence(task, freq, actor, end_date=None, max_occ=None):
    """Create a RecurringTaskRule from an existing task and link it as instance #1.

    Shared by the web Assign form and the native API. `freq` must be one of
    daily/weekly/monthly. End condition: an end_date wins, else a max_occ count,
    else it repeats forever. Returns the rule, or None if `freq` isn't recurring.
    """
    from ..models import RecurringTaskRule

    if freq not in (RecurringTaskRule.FREQ_DAILY, RecurringTaskRule.FREQ_WEEKLY,
                    RecurringTaskRule.FREQ_MONTHLY):
        return None

    anchor = task.due_date or timezone.localdate()
    weekdays = str(anchor.weekday()) if freq == RecurringTaskRule.FREQ_WEEKLY else ""
    checklist_template = "\n".join(c.title for c in task.checklist_items.all())
    subscriber_ids = ",".join(str(s.user_id) for s in task.subscribers.all())

    if end_date:
        end_type = RecurringTaskRule.END_ON
    elif max_occ:
        end_type = RecurringTaskRule.END_AFTER
    else:
        end_type = RecurringTaskRule.END_NEVER

    rule = RecurringTaskRule.objects.create(
        title=task.title, description=task.description, category=task.category,
        priority=task.priority, created_by=actor, assigned_to=task.assigned_to,
        due_time=task.due_time, checklist_template=checklist_template,
        subscriber_ids=subscriber_ids, frequency=freq, interval=1, weekdays=weekdays,
        start_date=anchor, end_type=end_type,
        end_date=end_date if end_type == RecurringTaskRule.END_ON else None,
        max_occurrences=max_occ if end_type == RecurringTaskRule.END_AFTER else None,
        occurrences_created=1, last_generated_date=anchor,
    )
    task.recurring_rule = rule
    task.repeat_rule = freq
    task.save(update_fields=["recurring_rule", "repeat_rule", "updated_at"])
    return rule

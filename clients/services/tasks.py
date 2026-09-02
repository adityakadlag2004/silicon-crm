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
import re
from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone

from ..models import NotificationPreference, Notification, TaskActivity


def log_activity(task, actor, action, detail=""):
    """Record one audit-trail entry for `task`. Returns the TaskActivity."""
    return TaskActivity.objects.create(
        task=task, actor=actor, action=action, detail=detail[:500],
    )


# ────────────────────── multi-assignee groups ──────────────────────
#
# Assigning one task to N people creates N sibling Task rows sharing an
# `assign_group`, so each person owns their own status and acknowledgement.
# These helpers present that group as the single task it really is.

def _display_name(employee):
    if not employee or not employee.user:
        return "Unassigned"
    return employee.user.get_full_name() or employee.user.username


def group_siblings(task):
    """All tasks in this task's assignment group (just itself when solo)."""
    from ..models import Task
    if not task.assign_group:
        return Task.objects.filter(pk=task.pk)
    return (Task.objects.filter(assign_group=task.assign_group, is_deleted=False)
            .select_related("assigned_to__user").order_by("pk"))


# Fields that describe the WORK, so they are identical across every sibling.
# Everything else (assigned_to, status, acknowledged_at, completed_at) is that
# one person's own business and is never propagated.
GROUP_SHARED_FIELDS = (
    "title", "description", "category", "priority", "due_date", "due_time",
    "client", "repeat_rule", "reminded_day_before", "reminded_same_day",
    "due_alarm_sent_at", "silent",
)


def apply_to_group(task, actor, fields):
    """Write `fields` (a dict of shared attributes) to every sibling.

    A three-person task is three rows. Editing the due date on the row you
    happen to be looking at used to move it for one person and leave the other
    two on the old deadline — while the Delegated list showed them collapsed as
    one row, hiding the split. Anything in [GROUP_SHARED_FIELDS] now moves
    together.

    Returns the number of sibling rows updated (excluding `task` itself).
    """
    shared = {k: v for k, v in fields.items() if k in GROUP_SHARED_FIELDS}
    if not shared or not task.assign_group:
        return 0
    siblings = group_siblings(task).exclude(pk=task.pk)
    n = 0
    for sib in siblings:
        for k, v in shared.items():
            setattr(sib, k, v)
        # A sibling sitting at Overdue whose deadline just moved forward is no
        # longer overdue — same rule the single-task edit path applies.
        if "due_date" in shared and sib.status == sib.STATUS_OVERDUE and not sib.is_overdue:
            sib.status = sib.STATUS_PENDING
        sib.save()
        n += 1
    return n


def delete_task(task, actor):
    """Soft-delete a task — and, when it is a group, the whole group.

    The assigner sees one row for "Mansi +2 others"; deleting it must not leave
    two live copies behind. Returns the number of rows deleted.
    """
    rows = list(group_siblings(task)) if task.assign_group else [task]
    if task.pk not in [t.pk for t in rows]:
        rows.append(task)
    for t in rows:
        t.is_deleted = True
        t.deleted_at = timezone.now()
        t.deleted_by = actor
        t.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
        log_activity(t, actor, TaskActivity.DELETED, "Moved to recycle bin.")
    return len(rows)


def sync_group_assignees(task, employee_ids, actor):
    """Reconcile who a task is assigned to, adding/removing sibling rows.

    `employee_ids` is the complete intended set. People removed have their row
    soft-deleted; people added get a fresh row carrying the same work (and a
    copy of the checklist), joined to the group. `task` keeps its own status
    and acknowledgement if its assignee survives.
    """
    from ..models import Employee, Task, TaskChecklistItem
    from uuid import uuid4

    wanted = [e for e in Employee.objects.filter(pk__in=employee_ids, active=True)]
    if not wanted:
        return
    rows = list(group_siblings(task)) if task.assign_group else [task]
    current = {t.assigned_to_id: t for t in rows}
    wanted_ids = {e.pk for e in wanted}

    # Several people from here on → the rows need a group id to travel as one.
    group = task.assign_group or (uuid4().hex if len(wanted) > 1 else "")
    if group and not task.assign_group:
        for t in rows:
            t.assign_group = group
            t.save(update_fields=["assign_group", "updated_at"])

    for emp_id, row in current.items():
        if emp_id not in wanted_ids:
            row.is_deleted = True
            row.deleted_at = timezone.now()
            row.deleted_by = actor
            row.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
            log_activity(row, actor, TaskActivity.DELETED, "Removed from this task.")

    checklist = list(task.checklist_items.values_list("title", "order"))
    for emp in wanted:
        if emp.pk in current:
            continue
        new = Task.objects.create(
            title=task.title, description=task.description, category=task.category,
            priority=task.priority, created_by=task.created_by, assigned_to=emp,
            due_date=task.due_date, due_time=task.due_time, client=task.client,
            assign_group=group, silent=task.silent,
        )
        for title, order in checklist:
            TaskChecklistItem.objects.create(task=new, title=title, order=order)
        log_activity(new, actor, TaskActivity.ASSIGNED, f"Assigned to {emp.user.username}.")
        notify_task(new, actor, "New task assigned",
                    f"{actor.username} assigned you “{new.title}”.", event="assigned")


def group_ack_roster(task):
    """Per-person acknowledgement for the group: who has seen this task.

    Returns [{"name", "acknowledged", "acknowledged_at", "status"}, …] — the
    detail page lists them by name instead of a single yes/no flag.
    """
    return [
        {
            "name": _display_name(t.assigned_to),
            "acknowledged": t.acknowledged_at is not None,
            "acknowledged_at": t.acknowledged_at,
            "status": t.get_status_display(),
        }
        for t in group_siblings(task) if t.assigned_to_id
    ]


def collapse_groups(tasks):
    """Collapse sibling tasks into one row each, keeping the first assignee.

    Annotates every returned task with `group_count` (people on it),
    `group_extra` (how many beyond the first) and `group_assignees` (all
    names), so a list can render "Mansi +4" instead of five identical rows.
    """
    by_group, members, out = {}, {}, []
    for t in tasks:
        t.group_count, t.group_extra, t.group_assignees = 1, 0, [_display_name(t.assigned_to)]
        if not t.assign_group:
            out.append(t)
            continue
        head = by_group.get(t.assign_group)
        if head is None:
            by_group[t.assign_group] = t
            members[t.assign_group] = [t]
            out.append(t)
            continue
        members[t.assign_group].append(t)
        head.group_count += 1
        head.group_extra += 1

    # Name the group after whoever was assigned first (lowest pk) — the list
    # may arrive in any sort order, but "Mansi +4" must stay stable.
    for gid, group in members.items():
        head = by_group[gid]
        head.group_assignees = [_display_name(t.assigned_to)
                                for t in sorted(group, key=lambda t: t.pk)]
        head.group_first_name = head.group_assignees[0]
    return out


def assignee_label(task):
    """"Mansi +4 others" for a collapsed group, plain name otherwise."""
    extra = getattr(task, "group_extra", 0)
    name = getattr(task, "group_first_name", None) or _display_name(task.assigned_to)
    if not extra:
        return name
    return f"{name} +{extra} other{'s' if extra > 1 else ''}"


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


# Task events that must RING on the phone like an alarm clock instead of a
# plain tray push (owner decision 2026-07-14, same as call follow-ups: the
# team misses silent notifications).
RINGING_EVENTS = {"assigned", "comment_added"}


def create_notification(user, title, body, link="", event=None, ring=True):
    """Create a Notification for `user` unless their prefs mute `event`.

    Returns the Notification, or None when muted / no user. Creating a
    Notification auto-mirrors an FCM push via signals.push_on_notification —
    except for RINGING_EVENTS, which suppress the plain mirror and send a
    data-only `task_alarm` push that the app turns into a ringing alert.

    `ring=False` (a task marked `silent`) keeps the plain mirror: the task
    still lands and the tray notification still pops, the phone just doesn't
    go off like an alarm at 11pm.
    """
    if not user or not user_wants(user, event):
        return None
    if event in RINGING_EVENTS and ring:
        notification = Notification(recipient=user, title=title, body=body, link=link)
        notification._skip_push = True  # the ringing push below replaces the mirror
        notification.save()
        from .push import send_data_push_to_user
        send_data_push_to_user(user, {
            "kind": "task_alarm",
            "title": title,
            "body": body,
            "link": link or "",
        })
        return notification
    return Notification.objects.create(recipient=user, title=title, body=body, link=link)


def ring_task(user, title, body, task, kind="task_alarm"):
    """One ringing push + a silent in-app Notification row, for `task`.

    The alarm-clock path used outside the assign/comment flow: due-time rings
    and renewal reminders. A task marked ``silent`` gets the ordinary
    Notification instead — signals.push_on_notification mirrors it as a plain
    tray push, so the assignee still hears about it without the phone going
    off at 11pm.
    """
    link = f"/clients/tasks/{task.pk}/"
    if task.silent:
        Notification.objects.create(recipient=user, title=title, body=body, link=link)
        return
    notification = Notification(recipient=user, title=title, body=body, link=link)
    notification._skip_push = True  # the ringing push below replaces the mirror
    notification.save()
    from .push import send_data_push_to_user
    send_data_push_to_user(user, {
        "kind": kind,
        "task_id": task.pk,
        "title": title,
        "body": body,
        "link": link,
    })


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


def notify_task(task, actor, title, body, event=None, exclude_users=None):
    """Fan out an in-app (+push) notification to everyone watching `task`.

    `actor` is excluded so the person who made the change is not pinged, as is
    anyone in `exclude_users` (e.g. @mentioned users who already got a more
    specific ping). Each recipient's NotificationPreference for `event` is
    respected. Deep links to the task detail page (the Android app routes
    /clients/tasks/… ).
    """
    link = _task_link(task)
    recipients = task_recipients(task, exclude_user=actor)
    excluded = {u.pk for u in (exclude_users or [])}
    sent = 0
    for user in recipients:
        if user.pk in excluded:
            continue
        if create_notification(user, title, body, link, event, ring=not task.silent):
            sent += 1
    return sent


_MENTION_RE = re.compile(r"@([A-Za-z0-9_.@+-]+)")


def notify_mentions(task, actor, text):
    """Ring every user @mentioned in a comment and loop them into the task.

    Matches @username tokens against real usernames; mentioned users are added
    as subscribers (so they see what follows) and get a personal ringing
    notification. Returns the mentioned users so the caller can exclude them
    from the generic comment fan-out (no double ring).
    """
    from django.contrib.auth.models import User
    from ..models import TaskSubscriber

    names = set(_MENTION_RE.findall(text or ""))
    if not names:
        return []
    mentioned = list(
        User.objects.filter(username__in=names, is_active=True)
        .exclude(pk=getattr(actor, "pk", None))
    )
    link = _task_link(task)
    for user in mentioned:
        TaskSubscriber.objects.get_or_create(task=task, user=user)
        create_notification(
            user, "You were mentioned",
            f"{actor.username} mentioned you on “{task.title}”.",
            link, event="comment_added", ring=not task.silent,
        )
    return mentioned


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

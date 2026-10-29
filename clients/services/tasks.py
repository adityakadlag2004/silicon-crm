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
        moved = any(k in shared and getattr(sib, k) != shared[k] for k in ("due_date", "due_time"))
        for k, v in shared.items():
            setattr(sib, k, v)
        if moved:
            # A sibling's own "already rung" flags belong to the old deadline.
            # Callers that move only the date (the sidebar, the calendar drag,
            # the phone) used to leave them set, so every assignee but the one
            # whose row was clicked never rang at the new time.
            sib.due_alarm_sent_at = None
            sib.reminded_day_before = False
            sib.reminded_same_day = False
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

    Deleting an instance of a repeating task also stops its series. A rule with
    no end date generates a fresh instance every night, so without this the only
    way to stop one was to find the instance and switch its Repeat to "No
    repeat" — deleting it just produced another one tomorrow, forever.
    """
    rows = list(group_siblings(task)) if task.assign_group else [task]
    if task.pk not in [t.pk for t in rows]:
        rows.append(task)
    rule = task.recurring_rule
    if rule and rule.is_active:
        rule.is_active = False
        rule.save(update_fields=["is_active"])
    now = timezone.now()   # one stamp for the group — restore_task matches on it
    for t in rows:
        t.is_deleted = True
        t.deleted_at = now
        t.deleted_by = actor
        t.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
        log_activity(t, actor, TaskActivity.DELETED, "Moved to recycle bin.")
    return len(rows)


def restore_task(task, actor):
    """Bring a task back from the recycle bin — the whole group, as it left.

    Deleting takes every sibling at once, so restoring one row used to bring
    back one person's copy and leave the rest in the bin. The siblings deleted
    in the same action come back (matched on `deleted_at`; a few seconds'
    slack covers rows deleted before the stamp was shared). A person removed
    from the task earlier stays removed. A repeating task's series, stopped by
    the delete, starts again. Returns the number of rows restored.
    """
    from ..models import Task

    rows = [task]
    if task.assign_group and task.deleted_at:
        slack = timedelta(seconds=5)
        rows += list(Task.objects.filter(
            assign_group=task.assign_group, is_deleted=True,
            deleted_at__range=(task.deleted_at - slack, task.deleted_at + slack),
        ).exclude(pk=task.pk))
    for t in rows:
        t.is_deleted, t.deleted_at, t.deleted_by = False, None, None
        t.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
        log_activity(t, actor, TaskActivity.RESTORED, "Restored from recycle bin.")
    rule = task.recurring_rule
    if rule and task.repeat_rule and not rule.is_active:
        rule.is_active = True     # generation re-checks the end conditions
        rule.save(update_fields=["is_active"])
    return len(rows)


def sync_group_assignees(task, employee_ids, actor):
    """Reconcile who a task is assigned to, adding/removing sibling rows.

    `employee_ids` is the complete intended set. A removed person's row is
    handed to an added person where there is one — a reassignment keeps the
    row, with its comments, files, history and ticks. Only then are leftover
    rows soft-deleted and leftover people given a fresh row carrying the same
    work (and a copy of the checklist), joined to the group.

    Swapping the assignee of a solo task through Edit used to bin the task and
    create a blank copy for the new person, and the page then opened the
    binned one.
    """
    from ..models import Employee, Task, TaskChecklistItem, TaskSubscriber
    from uuid import uuid4

    wanted = [e for e in Employee.objects.filter(pk__in=employee_ids, active=True)
              .select_related("user")]
    if not wanted:
        return
    rows = _group_rows(task)
    current = {t.assigned_to_id: t for t in rows}
    wanted_ids = {e.pk for e in wanted}

    # Several people from here on → the rows need a group id to travel as one.
    group = task.assign_group or (uuid4().hex if len(wanted) > 1 else "")
    if group and not task.assign_group:
        for t in rows:
            t.assign_group = group
            t.save(update_fields=["assign_group", "updated_at"])

    leaving = sorted((row for emp_id, row in current.items() if emp_id not in wanted_ids),
                     key=lambda r: r.pk != task.pk)      # `task` itself is handed on first
    joining = [e for e in wanted if e.pk not in current]
    while leaving and joining:
        row, emp = leaving.pop(0), joining.pop(0)
        row.assigned_to = emp
        # The new person has seen nothing yet: acknowledgement and the
        # due-time ring start again for them.
        row.acknowledged_at = row.ack_last_rung_at = row.due_alarm_sent_at = None
        row.save()
        log_activity(row, actor, TaskActivity.ASSIGNED, f"Reassigned to {emp.user.username}.")
        notify_task(row, actor, "New task assigned",
                    f"{actor.username} assigned you “{row.title}”.", event="assigned")

    for row in leaving:
        row.is_deleted = True
        row.deleted_at = timezone.now()
        row.deleted_by = actor
        row.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
        log_activity(row, actor, TaskActivity.DELETED, "Removed from this task.")

    checklist = list(task.checklist_items.values_list("title", "order"))
    watchers = list(task.subscribers.values_list("user_id", flat=True))
    for emp in joining:
        new = Task.objects.create(
            title=task.title, description=task.description, category=task.category,
            priority=task.priority, created_by=task.created_by, assigned_to=emp,
            due_date=task.due_date, due_time=task.due_time, client=task.client,
            assign_group=group, silent=task.silent,
            repeat_rule=task.repeat_rule, recurring_rule=task.recurring_rule,
        )
        for title, order in checklist:
            TaskChecklistItem.objects.create(task=new, title=title, order=order)
        for uid in watchers:
            TaskSubscriber.objects.get_or_create(task=new, user_id=uid)
        log_activity(new, actor, TaskActivity.ASSIGNED, f"Assigned to {emp.user.username}.")
        notify_task(new, actor, "New task assigned",
                    f"{actor.username} assigned you “{new.title}”.", event="assigned")


def _parse_date(raw):
    from datetime import datetime
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_time(raw):
    from datetime import datetime
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except (TypeError, ValueError):
        return None


# Editing a task is one submit, not eight. `data` is presence-sensitive: a key
# that is absent leaves that field alone, so the web form and the app's edit
# sheet can each send only what they actually show.
EDIT_FIELDS = (
    "title", "description", "priority", "category_id", "due_date", "due_time",
    "client_id", "assigned_to", "assignees", "repeat_rule", "silent",
    "subscribers", "checklist",
)


def apply_edit(task, actor, data):
    """Update `task` from a dict of submitted fields, as one logged change.

    The single implementation behind the web Edit modal and the app's edit
    sheet — the app carried the only copy, which is why a title typed wrong on
    the phone could not be fixed in a browser. Dates and times arrive as
    strings ("2026-09-04", "15:30") from both callers.
    """
    from ..models import Client, Employee, RecurringTaskRule, Task, TaskCategory

    if (data.get("title") or "").strip():
        task.title = data["title"].strip()[:255]
    if "description" in data:
        task.description = (data.get("description") or "").strip()
    if data.get("priority") in dict(Task.PRIORITY_CHOICES):
        task.priority = data["priority"]
    if "category_id" in data:
        cid = data.get("category_id")
        task.category = TaskCategory.objects.filter(pk=cid).first() if cid else None
    if "due_date" in data:
        new_date = _parse_date(data.get("due_date"))
        # Presence-sensitive like everything else here: a caller that sends
        # only a date is moving the day, not clearing the time.
        new_time = _parse_time(data.get("due_time")) if "due_time" in data else task.due_time
        if new_date != task.due_date or new_time != task.due_time:
            # New deadline → the reminders and the due-time ring must fire again.
            task.reminded_day_before = False
            task.reminded_same_day = False
            task.due_alarm_sent_at = None
        task.due_date = new_date
        task.due_time = new_time
        if task.status == Task.STATUS_OVERDUE and not task.is_overdue:
            task.status = Task.STATUS_PENDING
    if "client_id" in data:
        cid = data.get("client_id")
        task.client = Client.objects.filter(pk=cid).first() if cid else None
    # Assignees: a list reconciles the whole group (adding/removing sibling
    # rows); the legacy single id still works for a solo task.
    group_assignees = data.get("assignees")
    if isinstance(group_assignees, list):
        pass  # applied after save(), so new siblings copy the fresh fields
    elif "assigned_to" in data:
        aid = data.get("assigned_to")
        new_assignee = Employee.objects.filter(pk=aid, active=True).first() if aid else None
        if new_assignee != task.assigned_to:
            task.acknowledged_at = None  # a new assignee must acknowledge afresh
            task.ack_last_rung_at = None
        task.assigned_to = new_assignee
    if "repeat_rule" in data:
        freq = (data.get("repeat_rule") or "").strip()
        if freq != (task.repeat_rule or ""):
            task.repeat_rule = freq
            rule = task.recurring_rule
            if not freq:
                # Repeat switched off — stop generating future instances.
                if rule:
                    rule.is_active = False
                    rule.save(update_fields=["is_active"])
            elif rule:
                rule.frequency = freq
                rule.is_active = True
                rule.save(update_fields=["frequency", "is_active"])
            elif freq in dict(RecurringTaskRule.FREQ_CHOICES):
                build_recurrence(task, freq, actor)
    if "silent" in data:
        task.silent = bool(data["silent"])
    task.save()
    if isinstance(data.get("checklist"), list):
        sync_checklist(task, data["checklist"])
    # A multi-assignee task is N sibling rows — the shared fields must move
    # on all of them, or the other people keep the old title/deadline.
    apply_to_group(task, actor, {f: getattr(task, f) for f in GROUP_SHARED_FIELDS})
    if isinstance(group_assignees, list):
        ids = [int(x) for x in group_assignees if str(x).isdigit()]
        if ids:
            sync_group_assignees(task, ids, actor)
    # After the assignee sync, so a person just added is in the loop list too.
    if isinstance(data.get("subscribers"), list):
        set_subscribers(task, data["subscribers"])
    sync_rule_from_task(task)
    log_activity(task, actor, TaskActivity.DESCRIPTION_UPDATED, "Task details updated.")
    notify_task(task, actor, "Task updated",
                f"“{task.title}” was updated.", event="status_changed", group=True)
    return task


def sync_checklist(task, titles):
    """Make the checklist read `titles`, in that order, keeping every tick.

    The phone's Edit sheet sends the checklist as plain titles on every save.
    It used to be deleted and rebuilt from them, so fixing a typo in the title
    un-ticked every item the assignee had already done. Items are matched by
    title: a kept line keeps its tick, a new line starts unticked, a dropped
    line goes.
    """
    from ..models import TaskChecklistItem

    pool = {}
    for item in task.checklist_items.all():
        pool.setdefault(item.title, []).append(item)
    keep = []
    lines = [t.strip()[:255] for t in titles if (t or "").strip()]
    for order, title in enumerate(lines):
        item = pool[title].pop(0) if pool.get(title) else TaskChecklistItem(task=task, title=title)
        if item.pk is None or item.order != order:
            item.order = order
            item.save()
        keep.append(item.pk)
    task.checklist_items.exclude(pk__in=keep).delete()


def _group_rows(task):
    """Every row of `task`'s group, with `task` itself as the very object the
    caller holds — so a change made through one is never lost on a stale copy."""
    if not task.assign_group:
        return [task]
    return [task if t.pk == task.pk else t for t in group_siblings(task)]


def thread_ids(task):
    """Pks of every copy of a group task (`task` included, even if binned).

    Comments and files are one thread for the whole task. A comment lands on
    the row it was typed on, and files attached at assignment land on the
    first row only — read per row, the second and third assignee never saw the
    brief or the voice note the assigner recorded for all three.
    """
    return {task.pk} | {t.pk for t in _group_rows(task)}


def thread_comments(task):
    from ..models import TaskComment
    return TaskComment.objects.filter(task_id__in=thread_ids(task)).select_related("author")


def thread_attachments(task):
    from ..models import TaskAttachment
    return TaskAttachment.objects.filter(task_id__in=thread_ids(task)).select_related("uploaded_by")


def add_subscriber(task, actor, user):
    """Put `user` in the loop on the whole task (every sibling). True if new."""
    from ..models import TaskSubscriber

    created = False
    for row in _group_rows(task):
        created = TaskSubscriber.objects.get_or_create(task=row, user=user)[1] or created
    if created:
        log_activity(task, actor, TaskActivity.SUBSCRIBER_ADDED, f"Added {user.username} in loop.")
        create_notification(user, "Added to a task",
                            f"You're now in the loop on “{task.title}”.",
                            _task_link(task), event="subscriber_added")
    return created


def remove_subscriber(task, user_id):
    from ..models import TaskSubscriber

    TaskSubscriber.objects.filter(task__in=_group_rows(task), user_id=user_id).delete()


def set_subscribers(task, user_ids):
    """Replace the in-loop list on every sibling with `user_ids`.

    Subscribers are on the task, not on one person's copy of it: a watcher
    added to one row of a three-person task used to hear about one third of it.
    """
    from django.contrib.auth.models import User
    from ..models import TaskSubscriber

    wanted = set(User.objects.filter(pk__in=[int(u) for u in user_ids if str(u).isdigit()])
                 .values_list("pk", flat=True))
    rows = _group_rows(task)
    TaskSubscriber.objects.filter(task__in=rows).exclude(user_id__in=wanted).delete()
    for row in rows:
        for uid in wanted:
            TaskSubscriber.objects.get_or_create(task=row, user_id=uid)


# ───────────────────── one-field changes (web + phone) ─────────────────────
#
# The web sidebar and the phone's per-field actions each carried their own
# copy of these. The copies drifted: the phone's due/priority/category/
# description changes touched one row of a group task, so the other assignees
# kept the old deadline. One implementation each, called from both doors.

def set_status(task, actor, new):
    """Move one person's task to `new`, logging and notifying. True if changed.

    Status is that person's own business, so it is never applied to siblings.
    """
    from ..models import Task

    # "Pending" past the deadline is really Overdue — a reopen/reset must
    # never hide a blown due date.
    if new == Task.STATUS_PENDING and task.due_at and task.due_at < timezone.now():
        new = Task.STATUS_OVERDUE
    if new not in dict(Task.STATUS_CHOICES) or new == task.status:
        return False
    old = task.get_status_display()
    task.status = new
    # completed_at reflects the LATEST completion — cleared on reopen so
    # timeliness reports never show a stale first-completion date.
    task.completed_at = timezone.now() if new == Task.STATUS_COMPLETED else None
    # Touching the status proves the assignee has seen the task.
    if (task.acknowledged_at is None and task.assigned_to
            and task.assigned_to.user_id == getattr(actor, "pk", None)):
        task.acknowledged_at = timezone.now()
    task.save(update_fields=["status", "completed_at", "acknowledged_at", "updated_at"])
    if new == Task.STATUS_COMPLETED:
        log_activity(task, actor, TaskActivity.COMPLETED, "Marked completed.")
        notify_task(task, actor, "Task completed",
                    f"“{task.title}” was marked completed.", event="completed")
    else:
        log_activity(task, actor, TaskActivity.STATUS_CHANGED,
                     f"{old} → {task.get_status_display()}.")
        notify_task(task, actor, "Task status changed",
                    f"“{task.title}”: {old} → {task.get_status_display()}.", event="status_changed")
    return True


def _due_text(task):
    if not task.due_date:
        return "—"
    return f"{task.due_date:%d %b %Y}" + (f" {task.due_time:%H:%M}" if task.due_time else "")


def set_due(task, actor, due_date, due_time):
    """Give the task (and every sibling) a new deadline that rings afresh."""
    from ..models import Task

    task.due_date, task.due_time = due_date, due_time
    # A new deadline has not been rung yet. Without this the task keeps the
    # flag from the deadline it used to have and tasks_ring_due never rings it
    # again — a rescheduled task that goes silent is worse than no reminder.
    task.due_alarm_sent_at = None
    task.reminded_day_before = False
    task.reminded_same_day = False
    if task.status == Task.STATUS_OVERDUE and not task.is_overdue:
        task.status = Task.STATUS_PENDING
    task.save(update_fields=["due_date", "due_time", "status", "due_alarm_sent_at",
                             "reminded_day_before", "reminded_same_day", "updated_at"])
    apply_to_group(task, actor, {"due_date": due_date, "due_time": due_time})
    log_activity(task, actor, TaskActivity.DUE_CHANGED, f"Due {_due_text(task)}.")
    notify_task(task, actor, "Task due date changed",
                f"“{task.title}” is now due {_due_text(task)}.", event="due_changed", group=True)


def set_priority(task, actor, new):
    from ..models import Task

    if new not in dict(Task.PRIORITY_CHOICES) or new == task.priority:
        return False
    old = task.get_priority_display()
    task.priority = new
    task.save(update_fields=["priority", "updated_at"])
    apply_to_group(task, actor, {"priority": new})
    log_activity(task, actor, TaskActivity.PRIORITY_CHANGED, f"{old} → {task.get_priority_display()}.")
    notify_task(task, actor, "Task priority changed",
                f"“{task.title}”: priority {old} → {task.get_priority_display()}.",
                event="priority_changed", group=True)
    sync_rule_from_task(task)
    return True


def set_category(task, actor, category):
    task.category = category
    task.save(update_fields=["category", "updated_at"])
    apply_to_group(task, actor, {"category": category})
    log_activity(task, actor, TaskActivity.CATEGORY_CHANGED,
                 f"Category → {category.name if category else '—'}.")
    sync_rule_from_task(task)


def set_description(task, actor, text):
    task.description = (text or "").strip()
    task.save(update_fields=["description", "updated_at"])
    apply_to_group(task, actor, {"description": task.description})
    log_activity(task, actor, TaskActivity.DESCRIPTION_UPDATED, "Updated description.")
    sync_rule_from_task(task)


def acknowledge(task, actor):
    """The assignee says "I have seen this". True if it was news.

    Stops the 4-hourly re-ring of a high/critical task and tells the person
    who assigned it. Only the assignee can do it — that is what it means.
    """
    if not (task.assigned_to and task.assigned_to.user_id == getattr(actor, "pk", None)):
        raise PermissionError("Only the assignee can acknowledge.")
    if task.acknowledged_at is not None:
        return False
    task.acknowledged_at = timezone.now()
    task.save(update_fields=["acknowledged_at", "updated_at"])
    log_activity(task, actor, TaskActivity.ACKNOWLEDGED, "Acknowledged the task.")
    # A follow-up's creator is the inactive system user — nobody to tell.
    if task.created_by_id and task.created_by_id != actor.pk and task.created_by.is_active:
        create_notification(task.created_by, "Task acknowledged",
                            f"{actor.username} acknowledged “{task.title}”.",
                            _task_link(task), event="status_changed")
    return True


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


# What the person who handed the task out wants to hear without subscribing:
# that it is done, and what the assignee said. An assignee's "I need the PAN
# copy" used to reach nobody unless the assigner had put themselves in loop.
CREATOR_EVENTS = {"completed", "comment_added"}


def task_recipients(task, exclude_user=None, group=False, creator=False):
    """Users who should hear about a change: assignee + subscribers.

    `group` widens it to every sibling's assignee and subscribers — for a
    change to the shared work (deadline, priority, details), which moves all of
    them. `creator` adds whoever assigned the task. Inactive users (the system
    user that creates follow-ups, people who have left) are never notified.
    Excludes `exclude_user` (usually the actor) and de-duplicates by user id.
    """
    from ..models import TaskSubscriber

    rows = _group_rows(task) if group else [task]
    users = {}
    for row in rows:
        if row.assigned_to and row.assigned_to.user_id:
            users[row.assigned_to.user_id] = row.assigned_to.user
    for sub in TaskSubscriber.objects.filter(task__in=rows).select_related("user"):
        users[sub.user_id] = sub.user
    if creator and task.created_by_id:
        users[task.created_by_id] = task.created_by
    if exclude_user is not None:
        users.pop(getattr(exclude_user, "id", None), None)
    return [u for u in users.values() if u.is_active]


def _task_link(task):
    try:
        return reverse("clients:task_detail", args=[task.pk])
    except Exception:
        return ""


def notify_task(task, actor, title, body, event=None, exclude_users=None, group=False):
    """Fan out an in-app (+push) notification to everyone watching `task`.

    `actor` is excluded so the person who made the change is not pinged, as is
    anyone in `exclude_users` (e.g. @mentioned users who already got a more
    specific ping). Each recipient's NotificationPreference for `event` is
    respected. `group` reaches every assignee of a multi-person task (see
    task_recipients). Deep links to the task detail page (the Android app
    routes /clients/tasks/… ).
    """
    link = _task_link(task)
    recipients = task_recipients(task, exclude_user=actor, group=group,
                                 creator=event in CREATOR_EVENTS)
    excluded = {u.pk for u in (exclude_users or [])}
    sent = 0
    for user in recipients:
        if user.pk in excluded:
            continue
        if create_notification(user, title, body, link, event, ring=not task.silent):
            sent += 1
    return sent


def notify_from_cron(task, title, body, event=None, seen=None):
    """A cron's notice about one row: its assignee always hears; anyone else
    watching a group task hears once per run, not once per assignee.

    A manager in loop on a five-person task used to get five "Task overdue"
    notices for one deadline. Pass the same `seen` set for the whole run.
    """
    seen = set() if seen is None else seen
    link = _task_link(task)
    own = task.assigned_to.user_id if task.assigned_to_id else None
    for user in task_recipients(task):
        key = (task.assign_group or task.pk, user.pk)
        if user.pk != own and key in seen:
            continue
        seen.add(key)
        create_notification(user, title, body, link, event)


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
        # Counted from the start date, never from the last instance: stepping
        # from a clamped date (31 Jan → 28 Feb) slid the series to the 28th for
        # good. The same rule ExternalPolicy.events follows.
        start = rule.start_date
        k = max(1, ((after.year - start.year) * 12 + after.month - start.month) // interval)
        nxt = _add_months(start, k * interval)
        while nxt <= after:
            k += 1
            nxt = _add_months(start, k * interval)
        return nxt
    # custom → treat like daily-with-interval
    return after + timedelta(days=interval)


def _rule_finished(rule):
    from ..models import RecurringTaskRule
    return (rule.end_type == RecurringTaskRule.END_AFTER and rule.max_occurrences
            and rule.occurrences_created >= rule.max_occurrences)


def generate_rule_instances(rule, today=None):
    """Create this period's instance of `rule` once its due date has arrived.

    One instance per period, for everyone on the rule (a group rule makes a
    group, sharing an assign_group like a hand-made multi-person task). Missed
    periods are skipped, not backfilled: the cron down for a week, a rule
    restored from the bin or started on a past date used to produce one
    instantly-overdue task per missed day — up to sixty — each escalating to
    management. Only the latest due occurrence is created. Returns the tasks.
    """
    from uuid import uuid4

    from ..models import Employee, RecurringTaskRule, Task, TaskChecklistItem, TaskSubscriber

    today = today or timezone.localdate()
    if not rule.is_active:
        return []
    if _rule_finished(rule):
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        return []

    ends = rule.end_date if rule.end_type == RecurringTaskRule.END_ON else None
    nxt = next_due_date(rule, rule.last_generated_date or rule.start_date)
    if nxt > today:
        return []
    while True:
        later = next_due_date(rule, nxt)
        if later > today or (ends and later > ends):
            break
        nxt = later
    if ends and nxt > ends:
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        return []

    ids = rule.assignee_id_list() or ([rule.assigned_to_id] if rule.assigned_to_id else [])
    by_id = {e.pk: e for e in Employee.objects.filter(pk__in=ids, active=True).select_related("user")}
    assignees = [by_id[i] for i in ids if i in by_id] or [None]
    group = uuid4().hex if len(assignees) > 1 else ""
    checklist = [l.strip()[:255] for l in rule.checklist_template.splitlines() if l.strip()]

    created = []
    for assignee in assignees:
        task = Task.objects.create(
            title=rule.title, description=rule.description, category=rule.category,
            priority=rule.priority, created_by=rule.created_by, assigned_to=assignee,
            due_date=nxt, due_time=rule.due_time, repeat_rule=rule.frequency,
            recurring_rule=rule, assign_group=group,
        )
        for i, line in enumerate(checklist):
            TaskChecklistItem.objects.create(task=task, title=line, order=i)
        for uid in rule.subscriber_id_list():
            TaskSubscriber.objects.get_or_create(task=task, user_id=uid)
        log_activity(task, rule.created_by, TaskActivity.CREATED,
                     f"Auto-generated from a {rule.get_frequency_display().lower()} recurring rule.")
        created.append(task)

    rule.occurrences_created += 1
    rule.last_generated_date = nxt
    rule.is_active = not _rule_finished(rule)
    rule.save(update_fields=["occurrences_created", "last_generated_date", "is_active"])
    seen = set()   # one notice per watcher, however many people the rule covers
    for task in created:
        notify_from_cron(task, "Recurring task created",
                         f"“{task.title}” is due {nxt:%d %b}.", event="recurring_created", seen=seen)
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
    # A task for three people is one series for three people. Each sibling
    # used to get a rule of its own, so tomorrow's copies came back as three
    # unrelated tasks and deleting one stopped only that person's series.
    rows = _group_rows(task)
    assignee_ids = ",".join(str(t.assigned_to_id) for t in rows if t.assigned_to_id)

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
        subscriber_ids=subscriber_ids, assignee_ids=assignee_ids,
        frequency=freq, interval=1, weekdays=weekdays,
        start_date=anchor, end_type=end_type,
        end_date=end_date if end_type == RecurringTaskRule.END_ON else None,
        max_occurrences=max_occ if end_type == RecurringTaskRule.END_AFTER else None,
        occurrences_created=1, last_generated_date=anchor,
    )
    for row in rows:
        row.recurring_rule = rule
        row.repeat_rule = freq
        row.save(update_fields=["recurring_rule", "repeat_rule", "updated_at"])
    return rule


def sync_rule_from_task(task):
    """Carry an edit to a repeating task into the tasks still to come.

    The series used to keep whatever it was created with: retitle the daily
    report, hand it to someone else, change its time — and tomorrow's copy
    came back with the old title, to the old person, at the old time. Only
    the newest instance speaks for the series; editing last week's copy
    leaves the future alone.
    """
    rule = task.recurring_rule
    if not (rule and rule.is_active):
        return
    rows = _group_rows(task)
    newer = rule.instances.filter(created_at__gt=task.created_at, is_deleted=False) \
        .exclude(pk__in=[r.pk for r in rows])
    if newer.exists():
        return
    rule.title, rule.description = task.title, task.description
    rule.category, rule.priority, rule.due_time = task.category, task.priority, task.due_time
    rule.assigned_to = task.assigned_to
    rule.assignee_ids = ",".join(str(r.assigned_to_id) for r in rows if r.assigned_to_id)
    rule.subscriber_ids = ",".join(str(u) for u in task.subscribers.values_list("user_id", flat=True))
    rule.checklist_template = "\n".join(task.checklist_items.values_list("title", flat=True))
    rule.save()

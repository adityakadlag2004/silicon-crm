"""Task Management views — replaces the external "Automate Tasks" tool.

Pages (exact nav set): Dashboard, My Tasks, Delegated Tasks, Subscribed Tasks,
All Tasks, Deleted Tasks, Activities — plus a task detail page, the Assign Task
create endpoint, per-field action endpoints, and a Drive download proxy.

Scope: employees see tasks they created, are assigned, or subscribe to;
managers and admins see everything (admins also manage the recycle bin and
categories). Every mutation logs a TaskActivity and notifies watchers via
``services.tasks`` (which reuses the CRM Notification→FCM pipeline).
"""
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import (
    Employee,
    NotificationPreference,
    SavedTaskFilter,
    Task,
    TaskActivity,
    TaskAttachment,
    TaskCategory,
    TaskChecklistItem,
    TaskComment,
    TaskReminderSetting,
    TaskSubscriber,
)
from ..services.tasks import create_notification, log_activity, notify_mentions, notify_task
from .helpers import parse_date_param

# Priority order used for the "Priority" sort (Critical first).
_PRIORITY_RANK = Case(
    When(priority=Task.PRIORITY_CRITICAL, then=Value(0)),
    When(priority=Task.PRIORITY_HIGH, then=Value(1)),
    When(priority=Task.PRIORITY_MEDIUM, then=Value(2)),
    When(priority=Task.PRIORITY_LOW, then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)


# ─────────────────────────── permission helpers ───────────────────────────

def _emp(request):
    return getattr(request.user, "employee", None)


def _role(request):
    emp = _emp(request)
    return getattr(emp, "role", "") if emp else ""


def _is_admin(request):
    return request.user.is_superuser or _role(request) == "admin"


def _can_manage_all(request):
    """Admins and managers see/act across the whole team."""
    return request.user.is_superuser or _role(request) in ("admin", "manager")


def _can_edit(request, task):
    """Creator, assignee, or a manager/admin may edit a task."""
    if _can_manage_all(request):
        return True
    if task.created_by_id == request.user.id:
        return True
    return bool(task.assigned_to and task.assigned_to.user_id == request.user.id)


def _base_qs():
    return (
        Task.objects.filter(is_deleted=False)
        .select_related("category", "assigned_to__user", "created_by")
    )


def _scoped_qs(request):
    """Non-deleted tasks the current user is allowed to see."""
    qs = _base_qs()
    if _can_manage_all(request):
        return qs
    uid = request.user.id
    return qs.filter(
        Q(created_by_id=uid)
        | Q(assigned_to__user_id=uid)
        | Q(subscribers__user_id=uid)
    ).distinct()


def _visible_task_or_404(request, pk, include_deleted=False):
    qs = Task.objects.all() if include_deleted else Task.objects.filter(is_deleted=False)
    task = get_object_or_404(qs, pk=pk)
    if _can_manage_all(request):
        return task
    uid = request.user.id
    allowed = (
        task.created_by_id == uid
        or (task.assigned_to and task.assigned_to.user_id == uid)
        or task.subscribers.filter(user_id=uid).exists()
    )
    if not allowed:
        raise PermissionDenied("You cannot access this task.")
    return task


# ─────────────────────────── filtering / sorting ───────────────────────────

def _quick_range(key, today):
    """Return (start_date, end_date) for a named quick range, or (None, None)."""
    if key == "today":
        return today, today
    if key == "tomorrow":
        d = today + timedelta(days=1)
        return d, d
    if key == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if key == "this_month":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1)
    return None, None


def _apply_filters(qs, request):
    """Apply search/status/priority/category/assignee/date/sort GET params."""
    q = (request.GET.get("q") or "").strip()
    if q:
        cond = (
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(category__name__icontains=q)
            | Q(assigned_to__user__username__icontains=q)
            | Q(created_by__username__icontains=q)
        )
        if q.lstrip("#").isdigit():
            cond |= Q(pk=int(q.lstrip("#")))
        qs = qs.filter(cond)

    status = request.GET.get("status")
    if status in dict(Task.STATUS_CHOICES):
        qs = qs.filter(status=status)

    priority = request.GET.get("priority")
    if priority in dict(Task.PRIORITY_CHOICES):
        qs = qs.filter(priority=priority)

    category = request.GET.get("category")
    if category and category.isdigit():
        qs = qs.filter(category_id=int(category))

    assignee = request.GET.get("assigned_to")
    if assignee and assignee.isdigit():
        qs = qs.filter(assigned_to_id=int(assignee))

    creator = request.GET.get("created_by")
    if creator and creator.isdigit():
        qs = qs.filter(created_by_id=int(creator))

    # Date range on the due date — quick range wins, else custom from/to.
    today = timezone.localdate()
    start, end = _quick_range(request.GET.get("range", ""), today)
    if start is None:
        start = parse_date_param(request.GET.get("from"))
        end = parse_date_param(request.GET.get("to"))
    if start:
        qs = qs.filter(due_date__gte=start)
    if end:
        qs = qs.filter(due_date__lte=end)

    sort = request.GET.get("sort", "newest")
    sort_map = {
        "due": ("due_date", "due_time"),
        "created": ("created_at",),
        "newest": ("-created_at",),
        "oldest": ("created_at",),
        "status": ("status",),
        "title": ("title",),
    }
    if sort == "priority":
        qs = qs.annotate(_prank=_PRIORITY_RANK).order_by("_prank", "-created_at")
    else:
        qs = qs.order_by(*sort_map.get(sort, ("-created_at",)))
    return qs


def _query_without(request, *drop):
    """Current query string with the given params removed (for tab links)."""
    gp = request.GET.copy()
    for key in drop:
        gp.pop(key, None)
    return gp.urlencode()


def _list_context(request, active_tab, title):
    """Shared context for the list/kanban/calendar template."""
    return {
        "active_tab": active_tab,
        "page_title": title,
        "view_mode": request.GET.get("view", "list"),
        "categories": TaskCategory.objects.filter(is_active=True),
        "employees": Employee.objects.filter(active=True).select_related("user"),
        "all_users": User.objects.filter(is_active=True).order_by("username"),
        "saved_filters": SavedTaskFilter.objects.filter(user=request.user),
        "current_query": request.META.get("QUERY_STRING", ""),
        "base_query": _query_without(request, "status", "page"),
        "statuses": Task.STATUS_CHOICES,
        "priorities": Task.PRIORITY_CHOICES,
        "can_manage_all": _can_manage_all(request),
        "is_admin": _is_admin(request),
        # echo current filter values
        "f": {
            "q": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
            "priority": request.GET.get("priority", ""),
            "category": request.GET.get("category", ""),
            "assigned_to": request.GET.get("assigned_to", ""),
            "range": request.GET.get("range", ""),
            "from": request.GET.get("from", ""),
            "to": request.GET.get("to", ""),
            "sort": request.GET.get("sort", "newest"),
        },
    }


# ─────────────────────────────── the pages ───────────────────────────────

@login_required
def task_dashboard(request):
    scoped = _scoped_qs(request)
    counts = scoped.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Task.STATUS_PENDING)),
        in_progress=Count("id", filter=Q(status=Task.STATUS_IN_PROGRESS)),
        completed=Count("id", filter=Q(status=Task.STATUS_COMPLETED)),
        overdue=Count("id", filter=Q(status=Task.STATUS_OVERDUE)),
    )
    qs = _apply_filters(scoped, request)
    ctx = _list_context(request, "dashboard", "Tasks")
    ctx["counts"] = counts
    ctx.update(_board_ctx(request, qs, 200))
    return render(request, "tasks/dashboard.html", ctx)


@login_required
def task_my(request):
    emp = _emp(request)
    base = _base_qs().filter(assigned_to=emp) if emp else _base_qs().none()
    ctx = _list_context(request, "my", "My Tasks")
    ctx["counts"] = _status_counts(base)
    ctx.update(_board_ctx(request, _apply_filters(base, request), 300))
    return render(request, "tasks/list.html", ctx)


@login_required
def task_delegated(request):
    base = _base_qs().filter(created_by=request.user)
    ctx = _list_context(request, "delegated", "Delegated Tasks")
    ctx["counts"] = _status_counts(base)
    ctx.update(_board_ctx(request, _apply_filters(base, request), 300))
    return render(request, "tasks/list.html", ctx)


@login_required
def task_subscribed(request):
    base = _base_qs().filter(subscribers__user=request.user).distinct()
    ctx = _list_context(request, "subscribed", "Subscribed Tasks")
    ctx["counts"] = _status_counts(base)
    ctx.update(_board_ctx(request, _apply_filters(base, request), 300))
    return render(request, "tasks/list.html", ctx)


@login_required
def task_all(request):
    base = _scoped_qs(request)
    ctx = _list_context(request, "all", "All Tasks")
    ctx["counts"] = _status_counts(base)
    ctx.update(_board_ctx(request, _apply_filters(base, request), 400))
    return render(request, "tasks/list.html", ctx)


@login_required
def task_deleted(request):
    if not _can_manage_all(request):
        # Employees only see their own deleted tasks.
        qs = Task.objects.filter(is_deleted=True, created_by=request.user)
    else:
        qs = Task.objects.filter(is_deleted=True)
    qs = qs.select_related("category", "assigned_to__user", "deleted_by").order_by("-deleted_at")
    ctx = _list_context(request, "deleted", "Deleted Tasks")
    ctx.update({"tasks": qs[:300]})
    return render(request, "tasks/deleted.html", ctx)


@login_required
def task_activities(request):
    acts = TaskActivity.objects.select_related("task", "actor").order_by("-created_at")
    if not _can_manage_all(request):
        uid = request.user.id
        acts = acts.filter(
            Q(task__created_by_id=uid)
            | Q(task__assigned_to__user_id=uid)
            | Q(task__subscribers__user_id=uid)
        ).distinct()
    # Filters: action, actor, task.
    action = request.GET.get("action")
    if action in dict(TaskActivity.ACTION_CHOICES):
        acts = acts.filter(action=action)
    actor = request.GET.get("actor")
    if actor and actor.isdigit():
        acts = acts.filter(actor_id=int(actor))
    task_id = request.GET.get("task")
    if task_id and task_id.isdigit():
        acts = acts.filter(task_id=int(task_id))
    ctx = _list_context(request, "activities", "Activities")
    ctx.update({
        "activities": acts[:300],
        "actions": TaskActivity.ACTION_CHOICES,
        "users": User.objects.filter(is_active=True).order_by("username"),
    })
    return render(request, "tasks/activities.html", ctx)


def _kanban(items):
    """Bucket a task list into the four Kanban columns."""
    buckets = {
        Task.STATUS_PENDING: [],
        Task.STATUS_IN_PROGRESS: [],
        Task.STATUS_COMPLETED: [],
        Task.STATUS_OVERDUE: [],
    }
    for t in items:
        if t.status in buckets:
            buckets[t.status].append(t)
    return buckets


def _cal_events(items):
    """JSON-serializable events for the calendar view (tasks with a due date)."""
    return [
        {
            "id": t.pk,
            "title": t.title,
            "date": t.due_date.isoformat(),
            "priority": t.priority,
            "status": t.status,
        }
        for t in items if t.due_date
    ]


def _status_counts(qs):
    """Per-status counts for the status tab bar (across the page's scope)."""
    return qs.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Task.STATUS_PENDING)),
        in_progress=Count("id", filter=Q(status=Task.STATUS_IN_PROGRESS)),
        completed=Count("id", filter=Q(status=Task.STATUS_COMPLETED)),
        overdue=Count("id", filter=Q(status=Task.STATUS_OVERDUE)),
    )


def _board_ctx(request, qs, limit):
    """Materialize a task queryset once for list/kanban/calendar rendering.

    Annotates each task with `can_check` so the list can show a mark-done
    checkbox only for tasks the current user is allowed to complete.
    """
    items = list(qs[:limit])
    manage_all = _can_manage_all(request)
    uid = request.user.id
    for t in items:
        t.can_check = bool(
            manage_all
            or (t.assigned_to and t.assigned_to.user_id == uid)
            or t.created_by_id == uid
        )
    return {"tasks": items, "kanban": _kanban(items), "cal_events": _cal_events(items)}


@login_required
def task_detail(request, pk):
    task = _visible_task_or_404(request, pk, include_deleted=True)
    ctx = {
        "task": task,
        "page_title": f"#{task.pk} · {task.title}",
        "checklist": task.checklist_items.all(),
        "comments": task.comments.select_related("author"),
        "attachments": task.attachments.select_related("uploaded_by"),
        "subscribers": task.subscribers.select_related("user"),
        "activities": task.activities.select_related("actor")[:100],
        "categories": TaskCategory.objects.filter(is_active=True),
        "employees": Employee.objects.filter(active=True).select_related("user"),
        "all_users": User.objects.filter(is_active=True).order_by("username"),
        "statuses": Task.STATUS_CHOICES,
        "priorities": Task.PRIORITY_CHOICES,
        "can_edit": _can_edit(request, task),
        "is_admin": _is_admin(request),
    }
    return render(request, "tasks/detail.html", ctx)


# ─────────────────────────────── create ───────────────────────────────

def _parse_time(raw):
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except (TypeError, ValueError):
        return None


def _upload_attachment(task, uploaded_file, user, is_voice=False):
    """Upload one file to the task's Drive folder + create a TaskAttachment.

    Returns (attachment_or_None, error_message_or_None). Never raises.
    """
    from ..services.google_drive import (
        DriveNotConfigured,
        get_or_create_tasks_root,
        upload_file,
    )

    name = f"Task#{task.pk} · {uploaded_file.name}"
    try:
        parent = get_or_create_tasks_root()
        file_id, link = upload_file(parent, name, uploaded_file.content_type, uploaded_file)
    except DriveNotConfigured:
        return None, "Google Drive is not configured — attachment was not saved."
    except Exception:
        return None, f"Could not upload “{uploaded_file.name}”. Please try again."

    att = TaskAttachment.objects.create(
        task=task,
        uploaded_by=user,
        filename=uploaded_file.name[:255],
        mime=(uploaded_file.content_type or "")[:120],
        size=uploaded_file.size or 0,
        drive_file_id=file_id,
        drive_view_link=link,
        is_voice_note=is_voice,
    )
    return att, None


@login_required
@require_POST
def task_create(request):
    """Handle the Assign Task popup submission."""
    title = (request.POST.get("title") or "").strip()
    if not title:
        messages.error(request, "A task needs a title.")
        return redirect(request.META.get("HTTP_REFERER") or "clients:task_dashboard")

    priority = request.POST.get("priority")
    if priority not in dict(Task.PRIORITY_CHOICES):
        priority = Task.PRIORITY_MEDIUM

    category = None
    cat_id = request.POST.get("category")
    if cat_id and cat_id.isdigit():
        category = TaskCategory.objects.filter(pk=int(cat_id), is_active=True).first()

    # Multiple assignees → one task per person (each gets its own notification).
    emp_ids = [int(x) for x in request.POST.getlist("assigned_to") if x.isdigit()]
    assignees = list(Employee.objects.filter(pk__in=emp_ids, active=True)) or [None]

    description = (request.POST.get("description") or "").strip()
    due_date = parse_date_param(request.POST.get("due_date"))
    due_time = _parse_time(request.POST.get("due_time"))
    repeat_rule = (request.POST.get("repeat_rule") or "").strip()[:20]
    checklist_titles = [c.strip() for c in request.POST.getlist("checklist_item") if c.strip()]
    subscriber_ids = [int(u) for u in request.POST.getlist("subscribers") if u.isdigit()]

    created_tasks = []
    with transaction.atomic():
        for assignee in assignees:
            task = Task.objects.create(
                title=title[:255], description=description, category=category,
                priority=priority, created_by=request.user, assigned_to=assignee,
                due_date=due_date, due_time=due_time, repeat_rule=repeat_rule,
            )
            for i, ct in enumerate(checklist_titles):
                TaskChecklistItem.objects.create(task=task, title=ct[:255], order=i)
            for uid in subscriber_ids:
                TaskSubscriber.objects.get_or_create(task=task, user_id=uid)
            log_activity(task, request.user, TaskActivity.CREATED, f"Created “{task.title}”.")
            if assignee:
                log_activity(task, request.user, TaskActivity.ASSIGNED,
                             f"Assigned to {assignee.user.username}.")
            created_tasks.append(task)

    first = created_tasks[0]

    # Attachments + voice note attach to the first task (best-effort).
    warnings = []
    for f in request.FILES.getlist("attachments"):
        _att, err = _upload_attachment(first, f, request.user)
        if err:
            warnings.append(err)
        else:
            log_activity(first, request.user, TaskActivity.ATTACHMENT_UPLOADED, f.name[:200])
    voice = request.FILES.get("voice_note")
    if voice:
        _att, err = _upload_attachment(first, voice, request.user, is_voice=True)
        if err:
            warnings.append(err)
        else:
            log_activity(first, request.user, TaskActivity.VOICENOTE_UPLOADED, "Voice note")

    for task in created_tasks:
        _maybe_create_recurrence(request, task)
        notify_task(task, request.user, "New task assigned",
                    f"{request.user.username} assigned you “{task.title}”.", event="assigned")

    if warnings:
        messages.warning(request, " ".join(warnings))
    if len(created_tasks) > 1:
        messages.success(request, f"Created {len(created_tasks)} tasks.")
        return redirect("clients:task_dashboard")
    messages.success(request, f"Task #{first.pk} created.")
    return redirect("clients:task_detail", pk=first.pk)


def _maybe_create_recurrence(request, task):
    """If the create form requested a repeat, spin up a RecurringTaskRule."""
    from ..services.tasks import build_recurrence

    freq = (request.POST.get("repeat_rule") or "").strip()
    end_date = parse_date_param(request.POST.get("repeat_end_date"))
    raw_max = request.POST.get("repeat_max")
    max_occ = int(raw_max) if (raw_max or "").isdigit() and int(raw_max) > 0 else None
    return build_recurrence(task, freq, request.user, end_date=end_date, max_occ=max_occ)


# ─────────────────────────────── actions ───────────────────────────────

def _guard_edit(request, task):
    if not _can_edit(request, task):
        return HttpResponseForbidden("You cannot edit this task.")
    return None


@login_required
@require_POST
def task_set_status(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    new = request.POST.get("status")
    # "Pending" past the deadline is really Overdue — a reopen/reset must
    # never hide a blown due date.
    if new == Task.STATUS_PENDING and task.due_at and task.due_at < timezone.now():
        new = Task.STATUS_OVERDUE
    if new not in dict(Task.STATUS_CHOICES) or new == task.status:
        return _back(request, task)
    old = task.get_status_display()
    task.status = new
    # completed_at reflects the LATEST completion — cleared on reopen so
    # timeliness reports never show a stale first-completion date.
    task.completed_at = timezone.now() if new == Task.STATUS_COMPLETED else None
    # Touching the status proves the assignee has seen the task.
    if (task.acknowledged_at is None and task.assigned_to
            and task.assigned_to.user_id == request.user.id):
        task.acknowledged_at = timezone.now()
    task.save(update_fields=["status", "completed_at", "acknowledged_at", "updated_at"])
    if new == Task.STATUS_COMPLETED:
        log_activity(task, request.user, TaskActivity.COMPLETED, "Marked completed.")
        notify_task(task, request.user, "Task completed",
                    f"“{task.title}” was marked completed.", event="completed")
    else:
        log_activity(task, request.user, TaskActivity.STATUS_CHANGED,
                     f"{old} → {task.get_status_display()}.")
        notify_task(task, request.user, "Task status changed",
                    f"“{task.title}”: {old} → {task.get_status_display()}.", event="status_changed")
    return _back(request, task)


@login_required
@require_POST
def task_set_priority(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    new = request.POST.get("priority")
    if new not in dict(Task.PRIORITY_CHOICES) or new == task.priority:
        return _back(request, task)
    old = task.get_priority_display()
    task.priority = new
    task.save(update_fields=["priority", "updated_at"])
    log_activity(task, request.user, TaskActivity.PRIORITY_CHANGED,
                 f"{old} → {task.get_priority_display()}.")
    notify_task(task, request.user, "Task priority changed",
                f"“{task.title}”: priority {old} → {task.get_priority_display()}.",
                event="priority_changed")
    return _back(request, task)


@login_required
@require_POST
def task_set_due(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    task.due_date = parse_date_param(request.POST.get("due_date"))
    task.due_time = _parse_time(request.POST.get("due_time"))
    # Reopen if a past-due overdue task got a fresh future date.
    if task.status == Task.STATUS_OVERDUE and not task.is_overdue:
        task.status = Task.STATUS_PENDING
    task.save(update_fields=["due_date", "due_time", "status", "updated_at"])
    log_activity(task, request.user, TaskActivity.DUE_CHANGED,
                 f"Due {task.due_date or '—'} {task.due_time or ''}".strip())
    notify_task(task, request.user, "Task due date changed",
                f"“{task.title}” is now due {task.due_date or '—'}.", event="due_changed")
    return _back(request, task)


@login_required
@require_POST
def task_set_category(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    cat_id = request.POST.get("category")
    task.category = (
        TaskCategory.objects.filter(pk=int(cat_id)).first()
        if cat_id and cat_id.isdigit() else None
    )
    task.save(update_fields=["category", "updated_at"])
    log_activity(task, request.user, TaskActivity.CATEGORY_CHANGED,
                 f"Category → {task.category.name if task.category else '—'}.")
    return _back(request, task)


@login_required
@require_POST
def task_add_subscriber(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    uid = request.POST.get("user")
    if uid and uid.isdigit():
        user = User.objects.filter(pk=int(uid)).first()
        if user:
            _sub, created = TaskSubscriber.objects.get_or_create(task=task, user=user)
            if created:
                log_activity(task, request.user, TaskActivity.SUBSCRIBER_ADDED,
                             f"Added {user.username} in loop.")
                create_notification(
                    user, "Added to a task",
                    f"You're now in the loop on “{task.title}”.",
                    reverse("clients:task_detail", args=[task.pk]),
                    event="subscriber_added",
                )
    return _back(request, task)


@login_required
@require_POST
def task_remove_subscriber(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    sub_id = request.POST.get("subscriber")
    if sub_id and sub_id.isdigit():
        TaskSubscriber.objects.filter(pk=int(sub_id), task=task).delete()
    return _back(request, task)


@login_required
@require_POST
def task_add_comment(request, pk):
    task = _visible_task_or_404(request, pk)
    body = (request.POST.get("body") or "").strip()
    if body:
        TaskComment.objects.create(task=task, author=request.user, body=body)
        log_activity(task, request.user, TaskActivity.COMMENT_ADDED, body[:200])
        # @mentioned users get a personal ring; the rest the generic one.
        mentioned = notify_mentions(task, request.user, body)
        notify_task(task, request.user, "New comment on a task",
                    f"{request.user.username} commented on “{task.title}”.",
                    event="comment_added", exclude_users=mentioned)
    return _back(request, task)


@login_required
@require_POST
def task_add_checklist(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    title = (request.POST.get("title") or "").strip()
    if title:
        order = task.checklist_items.count()
        TaskChecklistItem.objects.create(task=task, title=title[:255], order=order)
        log_activity(task, request.user, TaskActivity.CHECKLIST_UPDATED, f"Added “{title[:120]}”.")
    return _back(request, task)


@login_required
@require_POST
def task_toggle_checklist(request, pk, item_id):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    item = get_object_or_404(TaskChecklistItem, pk=item_id, task=task)
    item.is_done = not item.is_done
    if item.is_done:
        item.completed_by = request.user
        item.completed_at = timezone.now()
    else:
        item.completed_by = None
        item.completed_at = None
    item.save(update_fields=["is_done", "completed_by", "completed_at"])
    log_activity(task, request.user, TaskActivity.CHECKLIST_UPDATED,
                 f"{'✓' if item.is_done else '↺'} {item.title[:120]} ({task.checklist_percent}%)")
    if request.headers.get("HX-Request"):
        return JsonResponse({"ok": True, "percent": task.checklist_percent, "done": item.is_done})
    return _back(request, task)


@login_required
@require_POST
def task_upload_attachment(request, pk):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    is_voice = request.POST.get("is_voice") == "1"
    warnings = []
    files = request.FILES.getlist("attachments") or ([request.FILES["voice_note"]] if "voice_note" in request.FILES else [])
    for f in files:
        _att, err = _upload_attachment(task, f, request.user, is_voice=is_voice)
        if err:
            warnings.append(err)
        else:
            log_activity(
                task, request.user,
                TaskActivity.VOICENOTE_UPLOADED if is_voice else TaskActivity.ATTACHMENT_UPLOADED,
                f.name[:200],
            )
            notify_task(task, request.user,
                        "Voice note added" if is_voice else "Attachment added",
                        f"{request.user.username} added a file to “{task.title}”.",
                        event="voicenote_added" if is_voice else "attachment_added")
    if warnings:
        messages.warning(request, " ".join(warnings))
    # The Android app posts here too (multipart with the session cookie) and
    # needs JSON instead of the web redirect.
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"ok": not warnings, "warnings": warnings})
    return _back(request, task)


@login_required
@require_POST
def task_delete_attachment(request, pk, att_id):
    task = _visible_task_or_404(request, pk)
    guard = _guard_edit(request, task)
    if guard:
        return guard
    att = get_object_or_404(TaskAttachment, pk=att_id, task=task)
    from ..services.google_drive import delete_file
    delete_file(att.drive_file_id)
    att.delete()
    return _back(request, task)


@login_required
def task_attachment_download(request, att_id):
    """Proxy a Drive file's bytes so previews/playback work without Drive auth."""
    att = get_object_or_404(TaskAttachment, pk=att_id)
    # Reuse task visibility rules.
    _visible_task_or_404(request, att.task_id, include_deleted=True)
    from ..services.google_drive import DriveNotConfigured, stream_file
    try:
        content, mime = stream_file(att.drive_file_id)
    except DriveNotConfigured:
        return HttpResponse("Google Drive is not configured.", status=503)
    except Exception:
        return HttpResponse("File unavailable.", status=502)
    resp = HttpResponse(content, content_type=mime or att.mime or "application/octet-stream")
    disposition = "inline" if request.GET.get("dl") != "1" else "attachment"
    resp["Content-Disposition"] = f'{disposition}; filename="{att.filename}"'
    return resp


@login_required
@require_POST
def task_delete(request, pk):
    task = _visible_task_or_404(request, pk)
    if not (_can_manage_all(request) or task.created_by_id == request.user.id):
        return HttpResponseForbidden("You cannot delete this task.")
    task.is_deleted = True
    task.deleted_at = timezone.now()
    task.deleted_by = request.user
    task.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
    log_activity(task, request.user, TaskActivity.DELETED, "Moved to recycle bin.")
    messages.success(request, f"Task #{task.pk} moved to Deleted Tasks.")
    return redirect("clients:task_dashboard")


@login_required
@require_POST
def task_restore(request, pk):
    task = get_object_or_404(Task, pk=pk, is_deleted=True)
    if not (_can_manage_all(request) or task.created_by_id == request.user.id):
        return HttpResponseForbidden("You cannot restore this task.")
    task.is_deleted = False
    task.deleted_at = None
    task.deleted_by = None
    task.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
    log_activity(task, request.user, TaskActivity.RESTORED, "Restored from recycle bin.")
    messages.success(request, f"Task #{task.pk} restored.")
    return redirect("clients:task_detail", pk=task.pk)


@login_required
@require_POST
def task_purge(request, pk):
    """Permanently delete — admins only."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    task = get_object_or_404(Task, pk=pk, is_deleted=True)
    from ..services.google_drive import delete_file
    for att in task.attachments.all():
        delete_file(att.drive_file_id)
    tid = task.pk
    task.delete()
    messages.success(request, f"Task #{tid} permanently deleted.")
    return redirect("clients:task_deleted")


def _back(request, task):
    """Redirect back to the referring page, else the task detail."""
    ref = request.META.get("HTTP_REFERER")
    if ref:
        return redirect(ref)
    return redirect("clients:task_detail", pk=task.pk)


# ─────────────────────────── category management ───────────────────────────

@login_required
def task_categories(request):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = (request.POST.get("name") or "").strip()
            if name:
                TaskCategory.objects.get_or_create(
                    name=name[:80],
                    defaults={
                        "color": (request.POST.get("color") or "#E5B740")[:7],
                        "icon": (request.POST.get("icon") or "bi-folder-fill")[:40],
                        "description": (request.POST.get("description") or "")[:255],
                        "created_by": request.user,
                    },
                )
                messages.success(request, f"Category “{name}” added.")
        elif action == "toggle":
            cid = request.POST.get("id")
            cat = TaskCategory.objects.filter(pk=cid).first() if cid else None
            if cat:
                cat.is_active = not cat.is_active
                cat.save(update_fields=["is_active", "updated_at"])
        elif action == "delete":
            cid = request.POST.get("id")
            TaskCategory.objects.filter(pk=cid).delete()
            messages.success(request, "Category deleted.")
        return redirect("clients:task_categories")

    ctx = _list_context(request, "categories", "Task Categories")
    # Category page shows *all* categories (active + inactive), overriding the
    # active-only list from _list_context.
    ctx["categories"] = TaskCategory.objects.all()
    return render(request, "tasks/categories.html", ctx)


@login_required
@require_POST
def task_category_create(request):
    """Quick-create a task category from the Assign Task dropdown (AJAX JSON)."""
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    cat, _created = TaskCategory.objects.get_or_create(
        name=name[:80],
        defaults={
            "color": (request.POST.get("color") or "#E5B740")[:7],
            "icon": (request.POST.get("icon") or "bi-folder-fill")[:40],
            "created_by": request.user,
        },
    )
    return JsonResponse({"ok": True, "id": cat.id, "name": cat.name, "color": cat.color})


# ─────────────────────────── saved filters ───────────────────────────

@login_required
@require_POST
def task_save_filter(request):
    name = (request.POST.get("name") or "").strip()
    query = (request.POST.get("query_string") or "").strip().lstrip("?")
    if name:
        SavedTaskFilter.objects.update_or_create(
            user=request.user, name=name[:80],
            defaults={"query_string": query[:500]},
        )
        messages.success(request, f"Saved filter “{name}”.")
    return redirect(request.META.get("HTTP_REFERER") or "clients:task_dashboard")


@login_required
@require_POST
def task_delete_filter(request, filter_id):
    SavedTaskFilter.objects.filter(pk=filter_id, user=request.user).delete()
    return redirect(request.META.get("HTTP_REFERER") or "clients:task_dashboard")


# ─────────────────────── settings: prefs + reminders ───────────────────────

_PREF_FIELDS = [
    ("notify_assigned", "Task assigned to me"),
    ("notify_status_changed", "Status changed"),
    ("notify_due_changed", "Due date changed"),
    ("notify_priority_changed", "Priority changed"),
    ("notify_comment_added", "New comment"),
    ("notify_checklist_updated", "Checklist updated"),
    ("notify_attachment_added", "Attachment added"),
    ("notify_voicenote_added", "Voice note added"),
    ("notify_completed", "Task completed"),
    ("notify_subscriber_added", "Added in loop"),
    ("notify_overdue", "Task overdue"),
    ("notify_recurring_created", "Recurring task created"),
]


@login_required
def task_settings(request):
    # Task notification/reminder settings are hidden from employees.
    if not _can_manage_all(request):
        return HttpResponseForbidden("Not available for your role.")
    pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
    reminder = TaskReminderSetting.current()

    if request.method == "POST":
        scope = request.POST.get("scope")
        if scope == "prefs":
            for field, _label in _PREF_FIELDS:
                setattr(pref, field, request.POST.get(field) == "on")
            pref.save()
            messages.success(request, "Notification preferences saved.")
        elif scope == "reminders" and _is_admin(request):
            reminder.remind_day_before = request.POST.get("remind_day_before") == "on"
            reminder.remind_same_day = request.POST.get("remind_same_day") == "on"
            hour = request.POST.get("same_day_hour")
            if hour and hour.isdigit() and 0 <= int(hour) <= 23:
                reminder.same_day_hour = int(hour)
            reminder.save()
            messages.success(request, "Reminder settings saved.")
        return redirect("clients:task_settings")

    ctx = _list_context(request, "settings", "Task Settings")
    ctx.update({
        "pref": pref,
        "pref_fields": [(f, label, getattr(pref, f)) for f, label in _PREF_FIELDS],
        "reminder": reminder,
    })
    return render(request, "tasks/settings.html", ctx)

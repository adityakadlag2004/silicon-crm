"""JSON API for the native Android Tasks + Business Links screens.

Same auth model as app_api.py (WebView session cookie + X-CSRFToken). Mirrors
the web behavior in clients/views/tasks.py and links.py, reusing the same
service helpers so activity logging and notifications stay identical.
"""
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import (
    Employee,
    Link,
    LinkCategory,
    LinkFavorite,
    Task,
    TaskActivity,
    TaskCategory,
    TaskChecklistItem,
    TaskComment,
    TaskSubscriber,
)
from ..services.tasks import build_recurrence, create_notification, log_activity, notify_task
from .helpers import parse_date_param


def _emp(request):
    return getattr(request.user, "employee", None)


def _role(request):
    emp = _emp(request)
    return getattr(emp, "role", "") if emp else ""


def _can_manage_all(request):
    return request.user.is_superuser or _role(request) in ("admin", "manager")


def _scoped(request):
    qs = Task.objects.filter(is_deleted=False).select_related(
        "category", "assigned_to__user", "created_by")
    if _can_manage_all(request):
        return qs
    uid = request.user.id
    return qs.filter(
        Q(created_by_id=uid) | Q(assigned_to__user_id=uid) | Q(subscribers__user_id=uid)
    ).distinct()


def _can_edit(request, task):
    if _can_manage_all(request):
        return True
    return (task.created_by_id == request.user.id
            or (task.assigned_to and task.assigned_to.user_id == request.user.id))


def _visible_or_404(request, pk):
    task = get_object_or_404(Task.objects.filter(is_deleted=False), pk=pk)
    if not _can_manage_all(request):
        uid = request.user.id
        allowed = (task.created_by_id == uid
                   or (task.assigned_to and task.assigned_to.user_id == uid)
                   or task.subscribers.filter(user_id=uid).exists())
        if not allowed:
            return None
    return task


def _task_row(task):
    return {
        "id": task.pk,
        "title": task.title,
        "status": task.status,
        "status_label": task.get_status_display(),
        "priority": task.priority,
        "priority_label": task.get_priority_display(),
        "category": task.category.name if task.category else None,
        "category_color": task.category.color if task.category else None,
        "assignee": (task.assigned_to.user.get_full_name() or task.assigned_to.user.username)
        if task.assigned_to else None,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_time": task.due_time.strftime("%H:%M") if task.due_time else None,
        "checklist_percent": task.checklist_percent,
    }


def _parse_time(raw):
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except (TypeError, ValueError):
        return None


# ─────────────────────────────── tasks ───────────────────────────────

@login_required
@require_GET
def app_tasks(request):
    tab = request.GET.get("tab", "all")
    emp = _emp(request)
    if tab == "my":
        qs = Task.objects.filter(is_deleted=False, assigned_to=emp) if emp else Task.objects.none()
    elif tab == "delegated":
        qs = Task.objects.filter(is_deleted=False, created_by=request.user)
    elif tab == "subscribed":
        qs = Task.objects.filter(is_deleted=False, subscribers__user=request.user).distinct()
    else:
        qs = _scoped(request)

    status = request.GET.get("status")
    if status in dict(Task.STATUS_CHOICES):
        qs = qs.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    if q:
        cond = Q(title__icontains=q) | Q(description__icontains=q) | Q(category__name__icontains=q)
        if q.lstrip("#").isdigit():
            cond |= Q(pk=int(q.lstrip("#")))
        qs = qs.filter(cond)

    category = request.GET.get("category")
    if category and category.isdigit():
        qs = qs.filter(category_id=int(category))
    priority = request.GET.get("priority")
    if priority in dict(Task.PRIORITY_CHOICES):
        qs = qs.filter(priority=priority)
    assignee = request.GET.get("assigned_to")
    if assignee and assignee.isdigit():
        qs = qs.filter(assigned_to_id=int(assignee))

    # Named date range on the due date.
    rng = request.GET.get("range", "")
    if rng:
        today = timezone.localdate()
        start = end = None
        if rng == "today":
            start = end = today
        elif rng == "tomorrow":
            start = end = today + timedelta(days=1)
        elif rng == "this_week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        elif rng == "this_month":
            start = today.replace(day=1)
            nxt = (start + timedelta(days=32)).replace(day=1)
            end = nxt - timedelta(days=1)
        if start:
            qs = qs.filter(due_date__gte=start, due_date__lte=end)

    qs = qs.select_related("category", "assigned_to__user").order_by("-created_at")
    counts = _scoped(request).aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Task.STATUS_PENDING)),
        in_progress=Count("id", filter=Q(status=Task.STATUS_IN_PROGRESS)),
        completed=Count("id", filter=Q(status=Task.STATUS_COMPLETED)),
        overdue=Count("id", filter=Q(status=Task.STATUS_OVERDUE)),
    )
    return JsonResponse({
        "counts": counts,
        "tasks": [_task_row(t) for t in qs[:200]],
    })


@login_required
@require_GET
def app_task_activities(request):
    """Recent task activity feed for the native Activities screen."""
    acts = TaskActivity.objects.select_related("task", "actor").order_by("-created_at")
    if not _can_manage_all(request):
        uid = request.user.id
        acts = acts.filter(
            Q(task__created_by_id=uid) | Q(task__assigned_to__user_id=uid)
            | Q(task__subscribers__user_id=uid)
        ).distinct()
    rows = [
        {
            "actor": a.actor.username if a.actor else "System",
            "action": a.get_action_display(),
            "detail": a.detail,
            "task_id": a.task_id,
            "task_title": a.task.title,
            "at": a.created_at.isoformat(),
        }
        for a in acts[:100]
    ]
    return JsonResponse({"activities": rows})


@login_required
@require_POST
def app_task_category_create(request):
    """Quick-create a task category from the native Assign screen."""
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "name required"}, status=400)
    cat, _ = TaskCategory.objects.get_or_create(
        name=name[:80],
        defaults={"color": (body.get("color") or "#E5B740")[:7],
                  "icon": "bi-folder-fill", "created_by": request.user},
    )
    return JsonResponse({"ok": True, "id": cat.id, "name": cat.name, "color": cat.color})


@login_required
@require_GET
def app_task_scorecard(request):
    """Per-member task completion stats for a period (day/week/month).

    Managers/admins get the whole team; employees get just themselves.
    Percentages are of tasks assigned within the window.
    """
    period = request.GET.get("period", "week")
    today = timezone.localdate()
    if period == "day":
        start = today
    elif period == "month":
        start = today.replace(day=1)
    else:
        period = "week"
        start = today - timedelta(days=today.weekday())

    emps = Employee.objects.filter(active=True).select_related("user")
    if not _can_manage_all(request):
        emp = _emp(request)
        emps = emps.filter(pk=emp.pk) if emp else emps.none()

    rows = []
    for e in emps:
        qs = Task.objects.filter(is_deleted=False, assigned_to=e, created_at__date__gte=start)
        total = qs.count()
        completed = qs.filter(status=Task.STATUS_COMPLETED).count()
        overdue = qs.filter(status=Task.STATUS_OVERDUE).count()
        pct = round(completed * 100 / total) if total else 0
        rows.append({
            "name": e.user.get_full_name() or e.user.username,
            "total": total,
            "completed": completed,
            "overdue": overdue,
            "completed_pct": pct,
            "not_completed_pct": 100 - pct if total else 0,
        })
    rows.sort(key=lambda r: (-r["completed_pct"], -r["total"]))
    return JsonResponse({"period": period, "scorecard": rows})


@login_required
@require_GET
def app_task_meta(request):
    return JsonResponse({
        "categories": [{"id": c.id, "name": c.name, "color": c.color}
                       for c in TaskCategory.objects.filter(is_active=True)],
        "employees": [{"id": e.id, "name": e.user.get_full_name() or e.user.username}
                      for e in Employee.objects.filter(active=True).select_related("user")],
        "users": [{"id": u.id, "name": u.get_full_name() or u.username}
                  for u in User.objects.filter(is_active=True)],
        "priorities": [{"value": v, "label": l} for v, l in Task.PRIORITY_CHOICES],
        "statuses": [{"value": v, "label": l} for v, l in Task.STATUS_CHOICES],
    })


@login_required
@require_GET
def app_task_detail(request, pk):
    task = _visible_or_404(request, pk)
    if task is None:
        return JsonResponse({"error": "forbidden"}, status=403)
    data = _task_row(task)
    data.update({
        "description": task.description,
        "created_by": task.created_by.username if task.created_by else None,
        "can_edit": _can_edit(request, task),
        "checklist": [{"id": i.id, "title": i.title, "done": i.is_done}
                      for i in task.checklist_items.all()],
        "comments": [{"author": c.author.username if c.author else "—",
                      "body": c.body, "at": c.created_at.isoformat()}
                     for c in task.comments.select_related("author")],
        "subscribers": [{"id": s.id, "name": s.user.get_full_name() or s.user.username}
                        for s in task.subscribers.select_related("user")],
        "attachments": [{"id": a.id, "filename": a.filename, "is_voice": a.is_voice_note,
                         "url": f"/clients/tasks/attachment/{a.id}/download/"}
                        for a in task.attachments.all()],
        "activities": [{"actor": a.actor.username if a.actor else "System",
                        "action": a.get_action_display(), "detail": a.detail,
                        "at": a.created_at.isoformat()}
                       for a in task.activities.select_related("actor")[:50]],
    })
    return JsonResponse(data)


@login_required
@require_POST
def app_task_create(request):
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "title required"}, status=400)

    priority = body.get("priority")
    if priority not in dict(Task.PRIORITY_CHOICES):
        priority = Task.PRIORITY_MEDIUM
    category = TaskCategory.objects.filter(pk=body.get("category_id"), is_active=True).first()

    # Accept a list of assignees ("assignees") or a single "assigned_to".
    raw_ids = body.get("assignees") or ([body.get("assigned_to")] if body.get("assigned_to") else [])
    emp_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    assignees = list(Employee.objects.filter(pk__in=emp_ids, active=True)) or [None]

    description = (body.get("description") or "").strip()
    due_date = parse_date_param(body.get("due_date"))
    due_time = _parse_time(body.get("due_time"))
    checklist = [c for c in (body.get("checklist") or []) if (c or "").strip()]
    subs = [int(u) for u in (body.get("subscribers") or []) if str(u).isdigit()]
    freq = (body.get("repeat_rule") or "").strip()

    created_ids = []
    for assignee in assignees:
        task = Task.objects.create(
            title=title[:255], description=description, category=category,
            priority=priority, created_by=request.user, assigned_to=assignee,
            due_date=due_date, due_time=due_time,
        )
        for i, ct in enumerate(checklist):
            TaskChecklistItem.objects.create(task=task, title=ct.strip()[:255], order=i)
        for uid in subs:
            TaskSubscriber.objects.get_or_create(task=task, user_id=uid)
        log_activity(task, request.user, TaskActivity.CREATED, f"Created “{task.title}”.")
        if assignee:
            log_activity(task, request.user, TaskActivity.ASSIGNED, f"Assigned to {assignee.user.username}.")
        if freq:
            build_recurrence(task, freq, request.user)
        notify_task(task, request.user, "New task assigned",
                    f"{request.user.username} assigned you “{task.title}”.", event="assigned")
        created_ids.append(task.pk)

    return JsonResponse({"ok": True, "id": created_ids[0], "ids": created_ids})


@login_required
@require_POST
def app_task_action(request, pk):
    task = _visible_or_404(request, pk)
    if task is None:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

    action = body.get("action")
    if not _can_edit(request, task) and action != "comment":
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    if action == "status":
        new = body.get("status")
        if new in dict(Task.STATUS_CHOICES) and new != task.status:
            old = task.get_status_display()
            task.status = new
            if new == Task.STATUS_COMPLETED:
                task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            if new == Task.STATUS_COMPLETED:
                log_activity(task, request.user, TaskActivity.COMPLETED, "Marked completed.")
                notify_task(task, request.user, "Task completed",
                            f"“{task.title}” was completed.", event="completed")
            else:
                log_activity(task, request.user, TaskActivity.STATUS_CHANGED,
                             f"{old} → {task.get_status_display()}.")
                notify_task(task, request.user, "Task status changed",
                            f"“{task.title}”: {old} → {task.get_status_display()}.",
                            event="status_changed")
    elif action == "priority":
        new = body.get("priority")
        if new in dict(Task.PRIORITY_CHOICES) and new != task.priority:
            old = task.get_priority_display()
            task.priority = new
            task.save(update_fields=["priority", "updated_at"])
            log_activity(task, request.user, TaskActivity.PRIORITY_CHANGED,
                         f"{old} → {task.get_priority_display()}.")
            notify_task(task, request.user, "Task priority changed",
                        f"“{task.title}” priority updated.", event="priority_changed")
    elif action == "comment":
        text = (body.get("body") or "").strip()
        if text:
            TaskComment.objects.create(task=task, author=request.user, body=text)
            log_activity(task, request.user, TaskActivity.COMMENT_ADDED, text[:200])
            notify_task(task, request.user, "New comment on a task",
                        f"{request.user.username} commented on “{task.title}”.",
                        event="comment_added")
    elif action == "checklist_toggle":
        item = TaskChecklistItem.objects.filter(pk=body.get("item_id"), task=task).first()
        if item:
            item.is_done = not item.is_done
            item.completed_by = request.user if item.is_done else None
            item.completed_at = timezone.now() if item.is_done else None
            item.save(update_fields=["is_done", "completed_by", "completed_at"])
            log_activity(task, request.user, TaskActivity.CHECKLIST_UPDATED,
                         f"{item.title[:120]} ({task.checklist_percent}%)")
    elif action == "add_checklist":
        title = (body.get("title") or "").strip()
        if title:
            TaskChecklistItem.objects.create(task=task, title=title[:255],
                                             order=task.checklist_items.count())
            log_activity(task, request.user, TaskActivity.CHECKLIST_UPDATED, f"Added “{title[:120]}”.")
    elif action == "due":
        task.due_date = parse_date_param(body.get("due_date"))
        task.due_time = _parse_time(body.get("due_time"))
        if task.status == Task.STATUS_OVERDUE and not task.is_overdue:
            task.status = Task.STATUS_PENDING
        task.save(update_fields=["due_date", "due_time", "status", "updated_at"])
        log_activity(task, request.user, TaskActivity.DUE_CHANGED, f"Due {task.due_date or '—'}.")
        notify_task(task, request.user, "Task due date changed",
                    f"“{task.title}” is now due {task.due_date or '—'}.", event="due_changed")
    elif action == "category":
        cid = body.get("category_id")
        task.category = TaskCategory.objects.filter(pk=cid).first() if cid else None
        task.save(update_fields=["category", "updated_at"])
        log_activity(task, request.user, TaskActivity.CATEGORY_CHANGED,
                     f"Category → {task.category.name if task.category else '—'}.")
    elif action == "description":
        task.description = (body.get("description") or "").strip()
        task.save(update_fields=["description", "updated_at"])
        log_activity(task, request.user, TaskActivity.DESCRIPTION_UPDATED, "Updated description.")
    elif action == "add_subscriber":
        uid = body.get("user_id")
        user = User.objects.filter(pk=uid).first() if uid else None
        if user:
            _sub, created = TaskSubscriber.objects.get_or_create(task=task, user=user)
            if created:
                log_activity(task, request.user, TaskActivity.SUBSCRIBER_ADDED,
                             f"Added {user.username} in loop.")
                create_notification(user, "Added to a task",
                                    f"You're now in the loop on “{task.title}”.",
                                    f"/clients/tasks/{task.pk}/", event="subscriber_added")
    elif action == "remove_subscriber":
        TaskSubscriber.objects.filter(pk=body.get("subscriber_id"), task=task).delete()
    elif action == "delete":
        if not (_can_manage_all(request) or task.created_by_id == request.user.id):
            return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
        task.is_deleted = True
        task.deleted_at = timezone.now()
        task.deleted_by = request.user
        task.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])
        log_activity(task, request.user, TaskActivity.DELETED, "Moved to recycle bin.")
    else:
        return JsonResponse({"ok": False, "error": "unknown action"}, status=400)

    return JsonResponse({"ok": True, "percent": task.checklist_percent, "status": task.status})


# ─────────────────────────────── links ───────────────────────────────

@login_required
@require_GET
def app_links(request):
    q = (request.GET.get("q") or "").strip()
    links = Link.objects.filter(is_active=True).select_related("category")
    if q:
        links = links.filter(Q(title__icontains=q) | Q(url__icontains=q) | Q(description__icontains=q))
    fav_ids = set(LinkFavorite.objects.filter(user=request.user).values_list("link_id", flat=True))

    def row(l):
        return {"id": l.id, "title": l.title, "url": l.url, "description": l.description,
                "icon": l.icon or (l.category.icon if l.category else "bi-link-45deg"),
                "category_id": l.category_id, "is_fav": l.id in fav_ids,
                "can_edit": request.user.is_superuser or _role(request) == "admin" or l.created_by_id == request.user.id}

    cats = [{"id": c.id, "name": c.name, "color": c.color, "icon": c.icon}
            for c in LinkCategory.objects.filter(is_active=True)]
    return JsonResponse({
        "categories": cats,
        "links": [row(l) for l in links[:400]],
        "favorites": [l.id for l in links if l.id in fav_ids],
    })


@login_required
@require_POST
def app_link_create(request):
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)
    title = (body.get("title") or "").strip()
    url = (body.get("url") or "").strip()
    if not title or not url:
        return JsonResponse({"ok": False, "error": "title and url required"}, status=400)
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    category = LinkCategory.objects.filter(pk=body.get("category_id")).first()
    link = Link.objects.create(
        title=title[:200], url=url[:500],
        description=(body.get("description") or "")[:255],
        category=category, icon=(body.get("icon") or "")[:40],
        created_by=request.user,
        display_order=Link.objects.filter(category=category).count(),
    )
    return JsonResponse({"ok": True, "id": link.id})


@login_required
@require_POST
def app_link_favorite(request, link_id):
    link = get_object_or_404(Link, pk=link_id)
    fav, created = LinkFavorite.objects.get_or_create(user=request.user, link=link)
    if not created:
        fav.delete()
    return JsonResponse({"ok": True, "is_fav": created})

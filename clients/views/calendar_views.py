"""Calendar views: calendar page, unified events feed, CRUD, task actions."""
import json
import logging
from datetime import datetime, time, timedelta

logger = logging.getLogger(__name__)

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST



from .. import permissions
from ..models import CalendarEvent, Client
from ..services import calendar_feed
from .helpers import throttle_view


@login_required
def employee_calendar(request):
    employee = request.user.employee
    now_ts = timezone.now()
    events = CalendarEvent.objects.filter(employee=employee)

    context = {
        "todays_events": events.filter(scheduled_time__date=now_ts.date()).order_by("scheduled_time"),
        "upcoming_events": events.filter(scheduled_time__date__gt=now_ts.date()).order_by("scheduled_time"),
        "pending_count": events.filter(status="pending").count(),
        "missed_count": events.filter(status="pending", scheduled_time__lt=now_ts).count(),
        "completed_count": events.filter(status="completed").count(),
    }
    return render(request, "calendar/employee_calendar.html", context)


@login_required
def employee_calendar_page(request):
    now_ts = timezone.now()
    events = CalendarEvent.objects.filter(employee=request.user.employee)
    context = {
        "pending_count": events.filter(status="pending").count(),
        "missed_count": events.filter(status="pending", scheduled_time__lt=now_ts).count(),
        "completed_count": events.filter(status="completed").count(),
    }
    return render(request, "calendar/employee_calendar.html", context)


def _parse_range_param(raw):
    dt = parse_datetime(raw) if raw else None
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


@require_GET
@login_required
def calendar_events_json(request):
    """Unified calendar feed for FullCalendar: manual events, birthdays,
    lead/call follow-ups and due tasks — one common calendar."""
    employee = request.user.employee
    start_dt = _parse_range_param(request.GET.get("start"))
    end_dt = _parse_range_param(request.GET.get("end"))

    sources_param = request.GET.get("sources")
    sources = None
    if sources_param:
        # legacy name from the old two-source calendar
        sources = ["event" if s == "manual" else s for s in sources_param.split(",") if s]

    items = calendar_feed.feed_items(employee, start=start_dt, end=end_dt, sources=sources)

    types_param = request.GET.get("types")
    if types_param:
        allowed_types = {t for t in types_param.split(",") if t}
        # type filters only constrain manual events (their user-picked type)
        # and birthdays; other sources have fixed types equal to their source.
        items = [
            it for it in items
            if it["source"] not in ("event", "birthday") or it["event_type"] in allowed_types
        ]

    statuses_param = request.GET.get("statuses")
    if statuses_param:
        allowed_statuses = {s for s in statuses_param.split(",") if s}
        items = [it for it in items if it["status"] in allowed_statuses]

    return JsonResponse(calendar_feed.to_fullcalendar(items), safe=False)


@require_GET
@login_required
def dashboard_agenda_json(request):
    """Unified agenda for the dashboard widget: everything dated, one feed.

    Employees see their own items. Admins/managers see team-wide lead and
    call follow-ups (optionally narrowed with ?employee_id=) plus their own
    events, tasks and client birthdays.
    """
    emp = getattr(request.user, "employee", None)
    if emp is None:
        return JsonResponse({"items": [], "week_dates": [], "today": ""})
    is_admin = permissions.is_admin_or_manager(request.user)
    employee_id = request.GET.get("employee_id") if is_admin else None

    now_ts = timezone.now()
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_dates = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]

    filter_mode = request.GET.get("filter", "this_week")
    day_start = timezone.make_aware(datetime.combine(today, time.min))
    if filter_mode == "today":
        start, end = day_start, day_start + timedelta(days=1)
    elif filter_mode == "tomorrow":
        start, end = day_start + timedelta(days=1), day_start + timedelta(days=2)
    elif filter_mode == "overdue":
        start, end = None, now_ts
    elif filter_mode == "all":
        start, end = None, day_start + timedelta(days=60)
    else:  # this_week — the week's items plus anything overdue from before it
        start = None
        end = timezone.make_aware(datetime.combine(week_start + timedelta(days=7), time.min))

    sources_param = request.GET.get("sources")
    sources = [s for s in sources_param.split(",") if s] if sources_param else None

    items = calendar_feed.feed_items(
        emp, start=start, end=end, sources=sources,
        team_followups=is_admin, employee_id=employee_id,
    )
    if filter_mode == "overdue":
        items = [it for it in items if it["is_overdue"]]
    elif filter_mode == "this_week":
        wk_start_dt = timezone.make_aware(datetime.combine(week_start, time.min))
        items = [it for it in items if it["start"] >= wk_start_dt or it["is_overdue"]]
    # pending things only on the dashboard — completed/skipped events stay off,
    # and past birthdays are history, not agenda items
    items = [
        it for it in items
        if it["status"] in ("pending", "missed")
        and (it["source"] != "birthday" or it["start"] >= day_start)
    ]

    return JsonResponse({
        "items": calendar_feed.to_agenda_json(items),
        "week_dates": week_dates,
        "today": today.isoformat(),
        "overdue_count": sum(1 for it in items if it["is_overdue"]),
    })


@login_required
@throttle_view(max_requests=60, window_seconds=60, key_prefix="calendar.update", json_response=True)
@require_POST
def update_calendar_event(request):
    """Handle drag/resize updates from FullCalendar."""
    try:
        data = json.loads(request.body)
        event_id = data.get("id")
        start = data.get("start")
        end = data.get("end")

        event = CalendarEvent.objects.get(id=event_id, employee=request.user.employee)

        if start:
            dt = parse_datetime(start)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            event.scheduled_time = dt or event.scheduled_time
        if end:
            end_dt = parse_datetime(end)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            event.end_time = end_dt or event.end_time

        event.save()
        return JsonResponse({"success": True})
    except Exception as e:
        logger.exception("Calendar operation failed")
        return JsonResponse({"success": False, "error": "Something went wrong — please try again."}, status=400)


@login_required
@throttle_view(max_requests=30, window_seconds=60, key_prefix="calendar.create", json_response=True)
@require_POST
def create_calendar_event(request):
    """Create a CalendarEvent via AJAX."""
    try:
        data = json.loads(request.body)
        title = data.get("title") or "Untitled"
        scheduled_time = data.get("scheduled_time")
        end_time = data.get("end_time") or data.get("end")
        ev_type = data.get("type") or "task"
        notes = data.get("notes") or ""
        related_prospect_id = data.get("related_prospect_id")
        client_id = data.get("client_id")
        user_employee = getattr(request.user, "employee", None)

        if not user_employee:
            return JsonResponse({"success": False, "error": "Employee profile not found for user."}, status=403)

        allowed_event_types = {choice[0] for choice in CalendarEvent.EVENT_TYPES}
        if ev_type not in allowed_event_types:
            return JsonResponse({"success": False, "error": "Invalid event type."}, status=400)

        scheduled_dt = None
        if scheduled_time:
            scheduled_dt = parse_datetime(scheduled_time)
            if scheduled_dt and timezone.is_naive(scheduled_dt):
                scheduled_dt = timezone.make_aware(scheduled_dt)
        else:
            scheduled_dt = timezone.now()

        # Calling component removed — prospects no longer exist.
        client = None
        restrict_to_own_records = not request.user.is_superuser and getattr(user_employee, "role", None) == "employee"

        if client_id:
            client_qs = Client.objects.all()
            if restrict_to_own_records:
                client_qs = client_qs.filter(mapped_to=user_employee)
            client = client_qs.filter(id=client_id).first()
            if client is None:
                return JsonResponse({"success": False, "error": "Client not found or permission denied."}, status=403)

        end_dt = None
        if end_time:
            end_dt = parse_datetime(end_time)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)

        event = CalendarEvent.objects.create(
            employee=user_employee,
            title=title,
            type=ev_type,
            notes=notes,
            scheduled_time=scheduled_dt,
            end_time=end_dt,
            client=client,
        )

        ev_json = {
            "id": event.id,
            "title": event.title,
            "start": event.scheduled_time.isoformat(),
            "end": event.end_time.isoformat() if event.end_time else None,
            "extendedProps": {
                "type": event.type,
                "notes": event.notes,
                "status": event.status,
                "related_prospect_id": None,
                "related_prospect_name": None,
            },
        }
        return JsonResponse({"success": True, "event": ev_json})
    except Exception as e:
        logger.exception("Calendar operation failed")
        return JsonResponse({"success": False, "error": "Something went wrong — please try again."}, status=400)


@login_required
@throttle_view(max_requests=30, window_seconds=60, key_prefix="calendar.delete", json_response=True)
@require_POST
def delete_calendar_event(request):
    """Delete a CalendarEvent owned by the requesting employee."""
    try:
        data = json.loads(request.body)
        event_id = data.get("id")
        if not event_id:
            return JsonResponse({"success": False, "error": "Missing id"}, status=400)

        if str(event_id).startswith("birth-"):
            return JsonResponse({"success": False, "error": "Birthday events cannot be deleted"}, status=400)

        event = CalendarEvent.objects.filter(id=event_id, employee=request.user.employee).first()
        if not event:
            return JsonResponse({"success": False, "error": "Event not found or permission denied"}, status=404)
        event.delete()
        return JsonResponse({"success": True})
    except Exception as e:
        logger.exception("Calendar operation failed")
        return JsonResponse({"success": False, "error": "Something went wrong — please try again."}, status=400)


@login_required
@throttle_view(max_requests=30, window_seconds=60, key_prefix="calendar.update_details", json_response=True)
@require_POST
def update_calendar_event_details(request):
    """Update title/type/notes/scheduled_time for an existing CalendarEvent."""
    try:
        data = json.loads(request.body)
        event_id = data.get("id")
        if not event_id:
            return JsonResponse({"success": False, "error": "Missing id"}, status=400)

        event = CalendarEvent.objects.filter(id=event_id, employee=request.user.employee).first()
        if not event:
            return JsonResponse({"success": False, "error": "Event not found or permission denied"}, status=404)

        title = data.get("title")
        scheduled_time = data.get("scheduled_time")
        ev_type = data.get("type")
        notes = data.get("notes")
        end_time = data.get("end_time") or data.get("end")

        if title is not None:
            event.title = title
        if ev_type is not None:
            event.type = ev_type
        if notes is not None:
            event.notes = notes
        if scheduled_time is not None:
            dt = parse_datetime(scheduled_time)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            event.scheduled_time = dt
        if end_time is not None:
            end_dt = parse_datetime(end_time)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            event.end_time = end_dt

        event.save()
        return JsonResponse({
            "success": True,
            "event": {
                "id": event.id,
                "title": event.title,
                "start": event.scheduled_time.isoformat(),
                "end": event.end_time.isoformat() if event.end_time else None,
                "extendedProps": {
                    "type": event.type,
                    "notes": event.notes,
                    "status": event.status,
                },
            },
        })
    except Exception as e:
        logger.exception("Calendar operation failed")
        return JsonResponse({"success": False, "error": "Something went wrong — please try again."}, status=400)


@login_required
def mark_done(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)
    event.status = "completed"
    event.save()
    messages.success(request, "Event marked as completed")
    return redirect("clients:employee_dashboard")


@login_required
def skip_event(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)
    event.status = "skipped"
    event.save()
    messages.warning(request, "Event skipped")
    return redirect("clients:employee_dashboard")


@login_required
def reschedule_event(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)

    if request.method == "POST":
        new_time = request.POST.get("scheduled_time")
        if new_time:
            parsed = parse_datetime(new_time)
            if parsed and timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            if parsed:
                event.scheduled_time = parsed
                event.status = "rescheduled"
                event.save()
                messages.success(request, "Event rescheduled")
                return redirect("clients:employee_dashboard")
            messages.error(request, "Could not parse new time; please use a valid date/time.")

    return render(request, "calendar/reschedule_event.html", {"event": event})

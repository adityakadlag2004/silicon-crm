"""Calendar views: calendar page, events JSON API, CRUD, task actions."""
import json
from datetime import date

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from ..models import CalendarEvent, Client, Prospect


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


@require_GET
@login_required
def calendar_events_json(request):
    """Returns calendar events as JSON for FullCalendar."""
    employee = request.user.employee
    start = request.GET.get("start")
    end = request.GET.get("end")
    types_param = request.GET.get("types")
    statuses_param = request.GET.get("statuses")
    sources_param = request.GET.get("sources")

    events_qs = CalendarEvent.objects.filter(employee=employee)
    if types_param:
        try:
            allowed = [t for t in types_param.split(",") if t]
            if allowed:
                events_qs = events_qs.filter(type__in=allowed)
        except Exception:
            pass

    start_dt = None
    end_dt = None
    if start:
        try:
            start_dt = parse_datetime(start)
            if start_dt and timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt)
            events_qs = events_qs.filter(scheduled_time__gte=start_dt)
        except Exception:
            pass

    if end:
        try:
            end_dt = parse_datetime(end)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            events_qs = events_qs.filter(scheduled_time__lte=end_dt)
        except Exception:
            pass

    now_ts = timezone.now()
    allowed_statuses = None
    if statuses_param:
        try:
            allowed_statuses = [s for s in statuses_param.split(",") if s]
        except Exception:
            allowed_statuses = None

    allowed_sources = None
    if sources_param:
        try:
            allowed_sources = [s for s in sources_param.split(",") if s]
        except Exception:
            allowed_sources = None

    events = []
    for e in events_qs:
        source = "manual"
        if e.related_prospect and getattr(e.related_prospect, "calling_list_id", None):
            source = "calling_list"

        status_val = e.status
        if e.status == "pending" and e.scheduled_time and e.scheduled_time < now_ts:
            status_val = "missed"

        if allowed_sources is not None and source not in allowed_sources:
            continue
        if allowed_statuses is not None and status_val not in allowed_statuses:
            continue

        events.append({
            "id": e.id,
            "title": e.title,
            "start": e.scheduled_time.isoformat(),
            "end": e.end_time.isoformat() if e.end_time else None,
            "extendedProps": {
                "type": e.type,
                "notes": e.notes,
                "status": status_val,
                "source": source,
                "related_prospect_id": e.related_prospect.id if e.related_prospect else None,
                "related_prospect_name": e.related_prospect.name if e.related_prospect else None,
            },
        })

    # Include birthdays from Clients and Prospects within the requested range
    try:
        if start_dt is None:
            s_dt = timezone.now() - timezone.timedelta(days=365)
        else:
            s_dt = start_dt
        if end_dt is None:
            e_dt = timezone.now() + timezone.timedelta(days=365)
        else:
            e_dt = end_dt

        def add_birthdays(queryset, label_prefix="Birthday"):
            for obj in queryset:
                dob = getattr(obj, "date_of_birth", None)
                if not dob:
                    continue
                for yr in range(s_dt.year, e_dt.year + 1):
                    try:
                        bday = date(yr, dob.month, dob.day)
                    except ValueError:
                        continue
                    bday_dt = timezone.make_aware(
                        timezone.datetime.combine(bday, timezone.datetime.min.time())
                    )
                    if s_dt <= bday_dt <= e_dt:
                        events.append({
                            "id": f"birth-{obj.__class__.__name__}-{obj.id}-{yr}",
                            "title": f"{label_prefix}: {getattr(obj, 'name', getattr(obj, 'client', ''))}",
                            "start": bday_dt.isoformat(),
                            "end": None,
                            "extendedProps": {
                                "type": "birthday",
                                "status": "pending",
                                "source": "birthday",
                                "notes": "Auto-generated birthday call",
                                "related_prospect_id": obj.id if obj.__class__.__name__ == "Prospect" else None,
                            },
                        })

        if not types_param or "birthday" in (types_param or ""):
            add_birthdays(Client.objects.filter(date_of_birth__isnull=False, mapped_to=employee))
            add_birthdays(
                Prospect.objects.filter(date_of_birth__isnull=False, assigned_to=employee),
                label_prefix="Prospect Birthday",
            )
    except Exception:
        pass

    return JsonResponse(events, safe=False)


@login_required
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
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
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

        scheduled_dt = None
        if scheduled_time:
            scheduled_dt = parse_datetime(scheduled_time)
            if scheduled_dt and timezone.is_naive(scheduled_dt):
                scheduled_dt = timezone.make_aware(scheduled_dt)
        else:
            scheduled_dt = timezone.now()

        related_prospect = None
        client = None
        if related_prospect_id:
            try:
                related_prospect = Prospect.objects.get(id=related_prospect_id)
            except Prospect.DoesNotExist:
                related_prospect = None
        if client_id:
            try:
                client = Client.objects.get(id=client_id)
            except Exception:
                client = None

        end_dt = None
        if end_time:
            end_dt = parse_datetime(end_time)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)

        event = CalendarEvent.objects.create(
            employee=request.user.employee,
            title=title,
            type=ev_type,
            notes=notes,
            scheduled_time=scheduled_dt,
            end_time=end_dt,
            related_prospect=related_prospect,
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
                "related_prospect_id": event.related_prospect.id if event.related_prospect else None,
                "related_prospect_name": event.related_prospect.name if event.related_prospect else None,
            },
        }
        return JsonResponse({"success": True, "event": ev_json})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
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
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
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
        return JsonResponse({"success": False, "error": str(e)}, status=400)


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

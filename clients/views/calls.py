"""Call tracking: sync API for the Android app, post-call follow-ups,
and the admin-only call analytics page.

The Android app (mobile/) listens for call state changes, and after each
call POSTs the event here. Follow-ups are created from the post-call popup;
a per-minute cron (send_followup_reminders) pushes the reminder, and tapping
the notification dials the number.
"""
import json
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import CallFollowUp, CallLogEntry, CallTrackingSettings, Client, Employee


def _user_emp(request):
    return getattr(request.user, "employee", None)


def _is_admin(request):
    emp = _user_emp(request)
    return request.user.is_superuser or (emp is not None and emp.role == "admin")


def _normalize_digits(phone):
    """Last 10 digits — good enough to match Indian numbers with/without +91/0."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _match_client(phone):
    digits = _normalize_digits(phone)
    if not digits:
        return None
    return Client.objects.filter(phone__endswith=digits).first()


def _within_work_hours(dt, settings_obj):
    local = timezone.localtime(dt)
    return settings_obj.work_start <= local.time() < settings_obj.work_end


# ── App-facing API ───────────────────────────────────────────────────────────

@login_required
@require_GET
def call_config(request):
    """The app fetches this at login to know the work-hours window
    (popup suppression outside office hours happens on-device)."""
    cfg = CallTrackingSettings.current()
    return JsonResponse({
        "enabled": cfg.enabled,
        "work_start_minutes": cfg.work_start.hour * 60 + cfg.work_start.minute,
        "work_end_minutes": cfg.work_end.hour * 60 + cfg.work_end.minute,
    })


@login_required
@require_POST
def calls_sync(request):
    """Batch-upsert call events from the app.

    Body: {"events": [{"phone", "direction", "connected", "duration_seconds",
                       "started_at" (ISO-8601 or epoch millis)}, ...]}
    """
    emp = _user_emp(request)
    if emp is None:
        return JsonResponse({"ok": False, "error": "no employee account"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
        events = data.get("events") or []
        assert isinstance(events, list)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

    created = 0
    for ev in events[:100]:  # sanity cap per request
        phone = str(ev.get("phone") or "").strip()[:32]
        direction = ev.get("direction")
        if not phone or direction not in (CallLogEntry.DIRECTION_INCOMING, CallLogEntry.DIRECTION_OUTGOING):
            continue

        raw_start = ev.get("started_at")
        started_at = None
        if isinstance(raw_start, (int, float)):
            started_at = datetime.fromtimestamp(raw_start / 1000.0, tz=dt_timezone.utc)
        elif isinstance(raw_start, str):
            try:
                started_at = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                if timezone.is_naive(started_at):
                    started_at = timezone.make_aware(started_at)
            except ValueError:
                pass
        if started_at is None:
            continue

        try:
            duration = max(0, int(ev.get("duration_seconds") or 0))
        except (TypeError, ValueError):
            duration = 0

        _, was_created = CallLogEntry.objects.get_or_create(
            employee=emp, phone=phone, started_at=started_at,
            defaults={
                "direction": direction,
                "connected": bool(ev.get("connected")),
                "duration_seconds": duration,
                "client": _match_client(phone),
            },
        )
        if was_created:
            created += 1

    return JsonResponse({"ok": True, "created": created})


# Quick-choice → delay from now. Server-side so phone clock skew doesn't matter.
_FOLLOWUP_CHOICES = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "tomorrow": None,   # next day 10:00
    "week": None,       # +7 days 10:00
}


@login_required
@require_POST
def call_followup_create(request):
    """Create a follow-up from the post-call popup.
    Body: {"phone": "...", "choice": "15m"|"1h"|"tomorrow"|"week", "note": "..."}"""
    emp = _user_emp(request)
    if emp is None:
        return JsonResponse({"ok": False, "error": "no employee account"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

    phone = str(data.get("phone") or "").strip()[:32]
    choice = data.get("choice")
    if not phone or choice not in _FOLLOWUP_CHOICES:
        return JsonResponse({"ok": False, "error": "phone and valid choice required"}, status=400)

    now = timezone.localtime()
    if choice == "tomorrow":
        scheduled = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif choice == "week":
        scheduled = (now + timedelta(days=7)).replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        scheduled = now + _FOLLOWUP_CHOICES[choice]

    fu = CallFollowUp.objects.create(
        employee=emp,
        phone=phone,
        client=_match_client(phone),
        scheduled_at=scheduled,
        note=str(data.get("note") or "").strip()[:255],
    )
    return JsonResponse({"ok": True, "id": fu.id, "scheduled_at": fu.scheduled_at.isoformat()})


# ── Employee-facing follow-up list ───────────────────────────────────────────

@login_required
def my_call_followups(request):
    emp = _user_emp(request)
    if emp is None:
        return HttpResponseForbidden("Need an employee account.")

    pending = CallFollowUp.objects.filter(
        employee=emp, status=CallFollowUp.STATUS_PENDING
    ).select_related("client").order_by("scheduled_at")
    recent_done = CallFollowUp.objects.filter(
        employee=emp, status__in=[CallFollowUp.STATUS_DONE, CallFollowUp.STATUS_DISMISSED]
    ).select_related("client").order_by("-completed_at")[:30]

    return render(request, "calls/my_followups.html", {
        "pending": pending,
        "recent_done": recent_done,
        "now": timezone.now(),
    })


@login_required
@require_POST
def call_followup_update(request, followup_id):
    """Mark a follow-up done/dismissed, or snooze it 1 hour."""
    emp = _user_emp(request)
    fu = get_object_or_404(CallFollowUp, id=followup_id)
    if not (_is_admin(request) or (emp and fu.employee_id == emp.id)):
        return HttpResponseForbidden("Not your follow-up.")

    action = request.POST.get("action")
    if action == "done":
        fu.status = CallFollowUp.STATUS_DONE
        fu.completed_at = timezone.now()
        fu.save(update_fields=["status", "completed_at"])
        messages.success(request, "Follow-up marked done.")
    elif action == "dismiss":
        fu.status = CallFollowUp.STATUS_DISMISSED
        fu.completed_at = timezone.now()
        fu.save(update_fields=["status", "completed_at"])
        messages.info(request, "Follow-up dismissed.")
    elif action == "snooze":
        fu.scheduled_at = timezone.now() + timedelta(hours=1)
        fu.reminded = False
        fu.save(update_fields=["scheduled_at", "reminded"])
        messages.success(request, "Snoozed 1 hour.")
    return redirect("clients:my_call_followups")


# ── Admin analytics ──────────────────────────────────────────────────────────

@login_required
def call_analytics(request):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")

    cfg = CallTrackingSettings.current()

    # Admin updates the work-hours window from the same page.
    if request.method == "POST" and request.POST.get("form") == "settings":
        try:
            cfg.work_start = datetime.strptime(request.POST.get("work_start", ""), "%H:%M").time()
            cfg.work_end = datetime.strptime(request.POST.get("work_end", ""), "%H:%M").time()
        except ValueError:
            messages.error(request, "Enter times as HH:MM.")
            return redirect("clients:call_analytics")
        cfg.enabled = request.POST.get("enabled") == "on"
        cfg.save()
        messages.success(request, "Call tracking settings saved.")
        return redirect("clients:call_analytics")

    today = timezone.localdate()

    def _parse_date(raw, default):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return default

    start = _parse_date(request.GET.get("start"), today)
    end = _parse_date(request.GET.get("end"), today)
    if end < start:
        start, end = end, start

    qs = CallLogEntry.objects.filter(
        started_at__date__gte=start,
        started_at__date__lte=end,
        # Personal calls outside office hours stay out of the numbers.
        started_at__time__gte=cfg.work_start,
        started_at__time__lt=cfg.work_end,
    )

    rows = (
        qs.values("employee_id", "employee__user__username",
                  "employee__user__first_name", "employee__user__last_name")
        .annotate(
            dialed=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING)),
            dialed_connected=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING, connected=True)),
            received=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_INCOMING, connected=True)),
            missed=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_INCOMING, connected=False)),
            talk_seconds=Sum("duration_seconds", filter=Q(connected=True)),
        )
        .order_by("-dialed")
    )
    rows = list(rows)
    for r in rows:
        r["talk_minutes"] = round((r["talk_seconds"] or 0) / 60, 1)
        name = f"{r['employee__user__first_name']} {r['employee__user__last_name']}".strip()
        r["display_name"] = name or r["employee__user__username"]

    totals = {
        "dialed": sum(r["dialed"] for r in rows),
        "dialed_connected": sum(r["dialed_connected"] for r in rows),
        "received": sum(r["received"] for r in rows),
        "missed": sum(r["missed"] for r in rows),
        "talk_minutes": round(sum(r["talk_seconds"] or 0 for r in rows) / 60, 1),
    }

    # Call log drill-down: independent employee + timeframe filters
    # (still work-hours scoped), latest 100.
    calls_range = request.GET.get("calls_range", "today")
    if calls_range not in ("today", "week", "month"):
        calls_range = "today"
    list_qs = CallLogEntry.objects.filter(
        started_at__time__gte=cfg.work_start,
        started_at__time__lt=cfg.work_end,
    )
    if calls_range == "today":
        list_qs = list_qs.filter(started_at__date=today)
    elif calls_range == "week":
        list_qs = list_qs.filter(started_at__date__gte=today - timedelta(days=6))
    else:
        list_qs = list_qs.filter(started_at__year=today.year, started_at__month=today.month)

    calls_emp = request.GET.get("calls_emp", "")
    try:
        calls_emp_id = int(calls_emp)
        list_qs = list_qs.filter(employee_id=calls_emp_id)
    except (TypeError, ValueError):
        calls_emp_id = None

    list_totals = list_qs.aggregate(
        dialed=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING)),
        connected_calls=Count("id", filter=Q(connected=True)),
        missed=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_INCOMING, connected=False)),
        talk_seconds=Sum("duration_seconds", filter=Q(connected=True)),
    )
    list_totals["talk_minutes"] = round((list_totals["talk_seconds"] or 0) / 60, 1)

    recent_calls = list_qs.select_related("employee__user", "client").order_by("-started_at")[:100]
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username")

    # Per-employee app/permission roster (reported by the app at each launch).
    from ..models import AppDeviceStatus
    statuses = {s.user_id: s for s in AppDeviceStatus.objects.select_related("user")}
    device_roster = []
    for emp in Employee.objects.filter(active=True).select_related("user").order_by("user__username"):
        if not emp.user_id:
            continue
        s = statuses.get(emp.user_id)
        device_roster.append({
            "name": emp.user.get_full_name() or emp.user.username,
            "status": s,  # None = app never opened / not installed
        })

    return render(request, "calls/call_analytics.html", {
        "rows": rows,
        "totals": totals,
        "start": start,
        "end": end,
        "cfg": cfg,
        "recent_calls": recent_calls,
        "device_roster": device_roster,
        "employees": employees,
        "calls_emp_id": calls_emp_id,
        "calls_range": calls_range,
        "list_totals": list_totals,
    })

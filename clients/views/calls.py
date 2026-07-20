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

from .. import permissions
from ..models import CallFollowUp, CallLogEntry, CallTrackingSettings, Client, Employee


def _user_emp(request):
    return getattr(request.user, "employee", None)


def _is_admin(request):
    return permissions.is_admin(request.user)


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
    """The app fetches this at launch. Two independent windows: tracking
    (which calls the device syncs / that analytics count) and the follow-up
    popup (which can run on more days, e.g. every day incl. Sunday)."""
    cfg = CallTrackingSettings.current()

    def _mins(t):
        return t.hour * 60 + t.minute

    return JsonResponse({
        # Tracking window
        "enabled": cfg.enabled,
        "work_start_minutes": _mins(cfg.work_start),
        "work_end_minutes": _mins(cfg.work_end),
        "work_days": cfg.work_day_list(),
        # Follow-up popup window (independent)
        "popup_enabled": cfg.popup_enabled,
        "popup_start_minutes": _mins(cfg.popup_start),
        "popup_end_minutes": _mins(cfg.popup_end),
        "popup_days": cfg.popup_day_list(),
        # Quick-chips the popup shows, admin-configured order. Keys unknown to
        # this server build are silently dropped so a stale setting can't
        # render a chip the create endpoint would reject.
        "popup_choices": [
            {"key": k, "label": _CATALOG_LABELS[k]}
            for k in cfg.popup_choice_list() if k in _CATALOG_LABELS
        ],
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


# Quick-choice → delay from now. Server-side so phone clock skew doesn't
# matter. Minute/hour choices fire at the exact offset; day+ choices fire at
# 10:00 on the target day (calling someone "in 10 days" at 9 PM is wrong);
# semantic choices resolve to a human moment ("today evening", "Monday").
_FOLLOWUP_MINUTES = {
    "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "3h": 180, "4h": 240,
}
_FOLLOWUP_DAYS = {
    "1d": 1, "5d": 5, "10d": 10,
    "1w": 7, "2w": 14, "1mo": 30, "2mo": 60,
    # Legacy keys from app v3.x popups still in the field:
    "tomorrow": 1, "week": 7,
}


def _sem_evening(now):
    """Today 6 PM; already evening (≥5:30 PM) → tomorrow 6 PM."""
    target = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now.hour > 17 or (now.hour == 17 and now.minute >= 30):
        target += timedelta(days=1)
    return target


def _sem_tomorrow_morning(now):
    return (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)


def _sem_monday_morning(now):
    days_ahead = (7 - now.weekday()) % 7 or 7  # today is Monday → next Monday
    return (now + timedelta(days=days_ahead)).replace(hour=10, minute=0, second=0, microsecond=0)


_FOLLOWUP_SEMANTIC = {
    "eve": _sem_evening,
    "tom_am": _sem_tomorrow_morning,
    "mon_am": _sem_monday_morning,
}

_FOLLOWUP_CHOICES = {**_FOLLOWUP_MINUTES, **_FOLLOWUP_DAYS, **_FOLLOWUP_SEMANTIC}

# Full chip catalog in canonical display order: (key, label). The admin picks
# a subset/order via CallTrackingSettings.popup_choices (app Settings screen);
# the popup renders whatever call_config sends. Legacy keys stay accepted by
# call_followup_create but are not offered here.
FOLLOWUP_CATALOG = [
    ("10m", "10 min"), ("15m", "15 min"), ("30m", "30 min"),
    ("1h", "1 hr"), ("2h", "2 hr"), ("3h", "3 hr"), ("4h", "4 hr"),
    ("eve", "Today 6 PM"), ("tom_am", "Tmrw 10 AM"), ("mon_am", "Mon 10 AM"),
    ("1d", "1 day"), ("5d", "5 days"), ("10d", "10 days"),
    ("1w", "1 week"), ("2w", "2 weeks"), ("1mo", "1 month"), ("2mo", "2 months"),
]
_CATALOG_LABELS = dict(FOLLOWUP_CATALOG)


def parse_custom_at(value):
    """Parse an exact moment picked on the phone → (datetime, error message).
    Naive timestamps are the user's wall-clock intent → server-local (IST)."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid custom_at"
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    now = timezone.localtime()
    if dt <= now or dt > now + timedelta(days=400):
        return None, "custom_at must be in the future"
    return dt, None


@login_required
@require_POST
def call_followup_create(request):
    """Create a follow-up from the post-call popup.
    Body: {"phone": "...", "choice": "15m"|"eve"|…, "note": "..."} — or, for
    the popup's date/time picker, {"phone": "...", "custom_at": ISO-8601}."""
    emp = _user_emp(request)
    if emp is None:
        return JsonResponse({"ok": False, "error": "no employee account"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

    phone = str(data.get("phone") or "").strip()[:32]
    choice = data.get("choice")
    custom_at = data.get("custom_at")
    if not phone or (choice not in _FOLLOWUP_CHOICES and not custom_at):
        return JsonResponse({"ok": False, "error": "phone and valid choice required"}, status=400)

    now = timezone.localtime()
    if custom_at:
        scheduled, err = parse_custom_at(custom_at)
        if err:
            return JsonResponse({"ok": False, "error": err}, status=400)
    elif choice in _FOLLOWUP_MINUTES:
        scheduled = now + timedelta(minutes=_FOLLOWUP_MINUTES[choice])
    elif choice in _FOLLOWUP_SEMANTIC:
        scheduled = _FOLLOWUP_SEMANTIC[choice](now)
    else:
        scheduled = (now + timedelta(days=_FOLLOWUP_DAYS[choice])).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

    fu = CallFollowUp.objects.create(
        employee=emp,
        phone=phone,
        client=_match_client(phone),
        scheduled_at=scheduled,
        note=str(data.get("note") or "").strip()[:255],
    )

    # One number = one reminder: the new follow-up supersedes any older
    # pending ones for the same employee + number (matched on the last 10
    # digits, so "+91XXXXXXXXXX" and "0XXXXXXXXXX" collapse). The app cancels
    # the superseded on-device alarms from the ids returned below.
    superseded_ids = []
    digits = _normalize_digits(phone)
    if digits:
        superseded_ids = [
            old.id
            for old in CallFollowUp.objects.filter(
                employee=emp, status=CallFollowUp.STATUS_PENDING
            ).exclude(pk=fu.pk)
            if _normalize_digits(old.phone) == digits
        ]
        if superseded_ids:
            CallFollowUp.objects.filter(pk__in=superseded_ids).delete()

    return JsonResponse({
        "ok": True,
        "id": fu.id,
        "scheduled_at": fu.scheduled_at.isoformat(),
        # Epoch millis so the popup can arm an exact on-device alarm.
        "scheduled_at_ms": int(fu.scheduled_at.timestamp() * 1000),
        "client": fu.client.name if fu.client_id else "",
        "note": fu.note,
        "superseded_ids": superseded_ids,
    })


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

    # Admin updates the tracking + popup windows from the same page.
    if request.method == "POST" and request.POST.get("form") == "settings":
        def _time(field, current):
            raw = request.POST.get(field, "")
            if not raw:
                return current
            try:
                return datetime.strptime(raw, "%H:%M").time()
            except ValueError:
                return current

        def _days(field, default):
            vals = [d for d in request.POST.getlist(field) if d.isdigit() and 0 <= int(d) <= 6]
            return ",".join(sorted(set(vals), key=int)) if vals else default

        cfg.work_start = _time("work_start", cfg.work_start)
        cfg.work_end = _time("work_end", cfg.work_end)
        cfg.popup_start = _time("popup_start", cfg.popup_start)
        cfg.popup_end = _time("popup_end", cfg.popup_end)
        cfg.enabled = request.POST.get("enabled") == "on"
        cfg.popup_enabled = request.POST.get("popup_enabled") == "on"
        cfg.work_days = _days("work_days", "0,1,2,3,4,5")
        cfg.popup_days = _days("popup_days", "0,1,2,3,4,5,6")
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
        started_at__week_day__in=cfg.work_week_days_django(),
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
        started_at__week_day__in=cfg.work_week_days_django(),
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
        "weekdays": [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")],
        "work_days_sel": cfg.work_day_list(),
        "popup_days_sel": cfg.popup_day_list(),
    })

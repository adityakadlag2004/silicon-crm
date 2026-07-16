"""Native app Settings screen: read + update module settings (admin only).

One GET returns every section; POST updates one section at a time
({"section": "call_tracking" | "popup" | "tasks", ...fields}) so a save in
one card can't clobber unsaved edits in another. Mirrors the web forms
(call_analytics settings + task_settings) — same singletons, same validation.
"""
import json
from datetime import time as datetime_time

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .. import permissions
from ..models import CallTrackingSettings, TaskReminderSetting
from .calls import FOLLOWUP_CATALOG


def _is_admin(request):
    return permissions.is_admin(request.user)


def _hhmm(t):
    return t.strftime("%H:%M")


def _parse_hhmm(raw, current):
    try:
        h, m = str(raw).split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return datetime_time(h, m)
    except (ValueError, AttributeError):
        pass
    return current


def _parse_days(raw, current):
    """Accept a JSON list of ints 0=Mon…6=Sun; empty/invalid keeps current."""
    if not isinstance(raw, list):
        return current
    days = sorted({int(d) for d in raw if isinstance(d, int) and 0 <= d <= 6})
    return ",".join(str(d) for d in days) if days else current


@login_required
@require_http_methods(["GET", "POST"])
def app_settings(request):
    if not _is_admin(request):
        return JsonResponse({"ok": False, "error": "Admins only."}, status=403)

    cfg = CallTrackingSettings.current()
    reminder = TaskReminderSetting.current()

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

        section = data.get("section")
        if section == "call_tracking":
            if "enabled" in data:
                cfg.enabled = bool(data["enabled"])
            cfg.work_start = _parse_hhmm(data.get("work_start"), cfg.work_start)
            cfg.work_end = _parse_hhmm(data.get("work_end"), cfg.work_end)
            cfg.work_days = _parse_days(data.get("work_days"), cfg.work_days)
            cfg.save()
        elif section == "popup":
            if "popup_enabled" in data:
                cfg.popup_enabled = bool(data["popup_enabled"])
            cfg.popup_start = _parse_hhmm(data.get("popup_start"), cfg.popup_start)
            cfg.popup_end = _parse_hhmm(data.get("popup_end"), cfg.popup_end)
            cfg.popup_days = _parse_days(data.get("popup_days"), cfg.popup_days)
            choices = data.get("popup_choices")
            if isinstance(choices, list):
                valid = dict(FOLLOWUP_CATALOG)
                keys = [k for k in choices if isinstance(k, str) and k in valid]
                if keys:  # never save an empty grid — the popup would be useless
                    cfg.popup_choices = ",".join(keys)
            cfg.save()
        elif section == "tasks":
            if "remind_day_before" in data:
                reminder.remind_day_before = bool(data["remind_day_before"])
            if "remind_same_day" in data:
                reminder.remind_same_day = bool(data["remind_same_day"])
            hour = data.get("same_day_hour")
            if isinstance(hour, int) and 0 <= hour <= 23:
                reminder.same_day_hour = hour
            reminder.save()
        else:
            return JsonResponse({"ok": False, "error": "unknown section"}, status=400)

    return JsonResponse({
        "ok": True,
        "call_tracking": {
            "enabled": cfg.enabled,
            "work_start": _hhmm(cfg.work_start),
            "work_end": _hhmm(cfg.work_end),
            "work_days": cfg.work_day_list(),
        },
        "popup": {
            "popup_enabled": cfg.popup_enabled,
            "popup_start": _hhmm(cfg.popup_start),
            "popup_end": _hhmm(cfg.popup_end),
            "popup_days": cfg.popup_day_list(),
            "popup_choices": cfg.popup_choice_list(),
            "catalog": [{"key": k, "label": lbl} for k, lbl in FOLLOWUP_CATALOG],
        },
        "tasks": {
            "remind_day_before": reminder.remind_day_before,
            "remind_same_day": reminder.remind_same_day,
            "same_day_hour": reminder.same_day_hour,
        },
    })

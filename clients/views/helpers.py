"""Shared helpers and utility functions used across view modules."""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from calendar import month_name
from functools import wraps

from django.shortcuts import get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.core.cache import cache
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.utils.timezone import now
from django.db.models import Sum

from ..models import (
    Lead,
    LeadFollowUp,
    Employee,
    ManagerAccessConfig,
)


def get_manager_access():
    return ManagerAccessConfig.current()


def _lead_queryset_for_request(request):
    qs = Lead.objects.select_related("assigned_to__user").prefetch_related(
        "progress_entries",
        "family_members",
    )
    emp = getattr(request.user, "employee", None)
    if emp and getattr(emp, "role", "") == "employee":
        qs = qs.filter(assigned_to=emp)
    return qs


def _parse_decimal(val):
    """Return Decimal or None for empty / non-numeric values."""
    if val is None or val == "":
        return None
    try:
        d = Decimal(str(val))
        return d if d >= 0 else None
    except Exception:
        return None


def is_admin(user):
    return hasattr(user, "employee") and user.employee.role == "admin"


def _last_n_months(today, n=12):
    """Return list of (year, month) tuples from oldest -> newest (n months including current)."""
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append((y, m))
        if m == 1:
            m = 12
            y -= 1
        else:
            m -= 1
    months.reverse()
    return months


def _client_ip(request):
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "unknown").strip()


def throttle_view(max_requests, window_seconds, key_prefix="throttle", methods=("POST",), json_response=False):
    """Simple cache-backed request throttling decorator.

    The key is scoped by user id (when authenticated) with IP fallback.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                return view_func(request, *args, **kwargs)

            identity = f"u{request.user.id}" if getattr(request.user, "is_authenticated", False) else f"ip{_client_ip(request)}"
            cache_key = f"{key_prefix}:{identity}"
            count = cache.get(cache_key, 0)

            if count >= max_requests:
                error_msg = "Too many requests. Please wait and try again."
                wants_json = json_response or request.content_type == "application/json"
                if wants_json:
                    return JsonResponse({"success": False, "error": error_msg}, status=429)
                messages.error(request, error_msg)
                referrer = request.META.get("HTTP_REFERER")
                if referrer:
                    return redirect(referrer)
                return HttpResponse(error_msg, status=429)

            if count == 0:
                cache.set(cache_key, 1, window_seconds)
            else:
                try:
                    cache.incr(cache_key)
                except ValueError:
                    cache.set(cache_key, count + 1, window_seconds)

            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator

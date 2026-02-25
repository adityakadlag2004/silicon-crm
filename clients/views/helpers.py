"""Shared helpers and utility functions used across view modules."""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from calendar import month_name

from django.shortcuts import get_object_or_404
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

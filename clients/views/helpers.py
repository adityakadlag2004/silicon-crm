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
from django.db.models import Sum, Q

from ..models import (
    Lead,
    LeadFollowUp,
    Employee,
    ManagerAccessConfig,
    Product,
)
# canonical definition lives in clients/permissions.py; re-exported here
# because many view modules import it from helpers
from ..permissions import is_admin  # noqa: F401


def name_words_q(field, query):
    """Match a name field that contains EVERY word in the query, in any order.

    People search by first + last name ("John Smith") but records often carry a
    middle name ("John Michael Smith"), so a plain `name__icontains="John Smith"`
    misses them. Splitting into words and AND-ing each as an icontains matches
    regardless of middle names or word order. One word behaves like a plain
    icontains; an empty query matches nothing extra (empty Q).
    """
    q = Q()
    for word in (query or "").split():
        q &= Q(**{f"{field}__icontains": word})
    return q


def get_manager_access():
    return ManagerAccessConfig.current()


# ── Sub-product roll-up ───────────────────────────────────────────────────────
# Reports show one column/row per top-level product CATEGORY. A sub-product
# ("Term Plan") folds into its parent ("Life Insurance") so the boards don't
# pile up dozens of sub-product columns.

def category_name_map():
    """{sub-product name -> parent category name}. Products with no parent are
    absent, so callers use `cat_map.get(name, name)`."""
    return dict(
        Product.objects.filter(parent__isnull=False).values_list("name", "parent__name")
    )


def product_totals(qs, cat_map):
    """Sum a Sale queryset by product category, highest amount first.

    Returns [{"product", "total_amount", "total_points"}] with sub-product rows
    merged into their parent's row.
    """
    out = {}
    for r in qs.values("product").annotate(total_amount=Sum("amount"), total_points=Sum("points")):
        cat = cat_map.get(r["product"], r["product"])
        row = out.setdefault(cat, {"product": cat, "total_amount": Decimal("0"), "total_points": Decimal("0")})
        row["total_amount"] += r["total_amount"] or Decimal("0")
        row["total_points"] += r["total_points"] or Decimal("0")
    return sorted(out.values(), key=lambda r: r["total_amount"], reverse=True)


def _lead_queryset_for_request(request):
    qs = Lead.objects.select_related("assigned_to__user").prefetch_related(
        "interests__product",
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


def parse_date_param(raw):
    """Parse a YYYY-MM-DD query param; None for anything malformed.
    Prevents 500s from raw GET values fed into __range lookups."""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


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


def _client_has_drive(client):
    return bool(getattr(client, "drive_folder_url", "") or getattr(client, "drive_folder_id", ""))


def success_with_drive_link(request, message, client, *, insurance):
    """Success message that, for insurance, links to the client's Drive folder
    so the policy document can be uploaded there.

    A link rather than a forced redirect: daily bulk entry shouldn't be
    interrupted, but the offer to file the policy is right there. The Drive
    view creates the folder on first click if it doesn't exist yet.
    """
    from django.urls import reverse
    from django.utils.safestring import mark_safe
    from django.utils.html import escape

    if insurance and client is not None:
        url = reverse("clients:client_drive_folder", args=[client.id])
        verb = "Open" if _client_has_drive(client) else "Create &amp; open"
        messages.success(request, mark_safe(
            f"{escape(message)} "
            f'<a href="{url}" target="_blank" rel="noopener" class="alert-link">'
            f'📎 {verb} {escape(client.name)}’s Drive folder to upload the policy</a>.'
        ))
    else:
        messages.success(request, message)

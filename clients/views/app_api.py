"""JSON API for the native Android screens (mobile/NATIVE_MIGRATION.md).

Same auth model as views/calls.py: the native app sends the WebView's
session cookie + X-CSRFToken header. One endpoint set is added here per
converted screen.
"""
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from ..models import CallFollowUp, Client, Notification, Sale


def _emp(request):
    return getattr(request.user, "employee", None)


def _is_admin(request):
    emp = _emp(request)
    return request.user.is_superuser or (emp is not None and emp.role == "admin")


def _money(value):
    """Decimal → float for JSON (display-only figures)."""
    return float(value or 0)


@login_required
@require_GET
def app_dashboard(request):
    """Everything the native dashboard screen needs, in one call."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    today = timezone.localdate()

    # Admins see the whole firm; everyone else sees their own numbers.
    sales_scope = Sale.objects.all() if is_admin else Sale.objects.filter(employee=emp)
    approved = sales_scope.filter(status=Sale.STATUS_APPROVED)

    today_agg = approved.filter(date=today).aggregate(n=Count("id"), amount=Sum("amount"))
    month_agg = approved.filter(date__year=today.year, date__month=today.month).aggregate(
        n=Count("id"), amount=Sum("amount"), points=Sum("points")
    )

    data = {
        "role": "admin" if is_admin else (emp.role if emp else "unknown"),
        "name": request.user.get_full_name() or request.user.username,
        "today": {
            "sales_count": today_agg["n"] or 0,
            "amount": _money(today_agg["amount"]),
        },
        "month": {
            "sales_count": month_agg["n"] or 0,
            "amount": _money(month_agg["amount"]),
            "points": _money(month_agg["points"]),
        },
        "unread_notifications": Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count(),
        "pending_followups": (
            CallFollowUp.objects.filter(
                employee=emp, status=CallFollowUp.STATUS_PENDING
            ).count()
            if emp else 0
        ),
        "recent_sales": [
            {
                "id": s.id,
                "client": s.client.name if s.client_id else "",
                "employee": s.employee.user.username if s.employee_id else "",
                "product": s.product or "",
                "amount": _money(s.amount),
                "status": s.status,
                "date": s.date.isoformat() if s.date else "",
            }
            for s in sales_scope.select_related("client", "employee__user")
            .order_by("-created_at")[:8]
        ],
    }

    if is_admin:
        data["pending_approvals"] = Sale.objects.filter(status=Sale.STATUS_PENDING).count()
        data["unmapped_clients"] = Client.objects.filter(mapped_to__isnull=True).count()

    return JsonResponse(data)

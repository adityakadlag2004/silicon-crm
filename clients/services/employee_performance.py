"""One employee's full record with the firm — business, earnings, pipeline, care.

The team detail page used to show six numbers off `Sale` and stop. Everything
here already had a service behind it (targets, incentives, the SPANCO funnel,
call outcomes); this is the one place that puts them against a single person so
their whole time with the firm can be read start to end.

Money and points are deliberately kept apart: `Sale.points` alone understates
what somebody earned, because multiyear health years land as `IncentiveAccrual`
rows and the FY ladder prize is released as a `BonusPayout`. Any "what I
earned" figure has to add all three.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth

from ..models import (
    BonusPayout, Client, EmployeeTarget, Lead, Renewal, Sale, Task,
)
from . import calls as calls_service
from . import incentives as incentives_service
from . import leads as lead_service

ZERO = Decimal("0")
_MONEY = DecimalField(max_digits=16, decimal_places=2)


def _money(qs, field="amount"):
    return qs.aggregate(t=Coalesce(Sum(field), Value(ZERO), output_field=_MONEY))["t"]


def fy_bounds(today):
    """The Indian financial year containing `today`, as (start, end)."""
    start_year = today.year if today.month >= 4 else today.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def month_bounds(today):
    return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])


def snapshot(emp, today=None):
    """Everything the team detail page shows about one employee.

    Roughly a dozen queries — small and fixed, not per-row — so it is safe to
    call on a page load.
    """
    today = today or date.today()
    month_start, month_end = month_bounds(today)
    fy_start, fy_end = fy_bounds(today)

    approved = Sale.objects.filter(employee=emp, status=Sale.STATUS_APPROVED)
    renewals = Renewal.objects.filter(employee=emp)

    # ---- business ---------------------------------------------------------
    business = {
        "sales_all": _money(approved),
        "sales_fy": _money(approved.filter(date__range=[fy_start, fy_end])),
        "sales_month": _money(approved.filter(date__range=[month_start, month_end])),
        "sales_count": approved.count(),
        "pending_count": Sale.objects.filter(employee=emp, status=Sale.STATUS_PENDING).count(),
        "rejected_count": Sale.objects.filter(employee=emp, status=Sale.STATUS_REJECTED).count(),
        "renewal_all": _money(renewals, "premium_amount"),
        "renewal_fy": _money(renewals.filter(
            premium_collected_on__range=[fy_start, fy_end]), "premium_amount"),
        "renewal_month": _money(renewals.filter(
            premium_collected_on__range=[month_start, month_end]), "premium_amount"),
        "renewal_count": renewals.count(),
    }
    business["total_all"] = business["sales_all"] + business["renewal_all"]
    business["total_fy"] = business["sales_fy"] + business["renewal_fy"]

    # Product mix for the financial year — what this person actually sells.
    mix = list(
        approved.filter(date__range=[fy_start, fy_end])
        .values("product").order_by()
        .annotate(amount=Sum("amount"), count=Count("id"))
        .order_by("-amount")
    )

    # ---- earnings (points = rupees) --------------------------------------
    # Sales points understate the truth on their own; accruals and hand-paid
    # ladder prizes are earned money too.
    sale_points = approved.aggregate(
        t=Coalesce(Sum("points"), Value(ZERO), output_field=_MONEY))["t"]
    accrued = incentives_service.accrued_points(emp, date(2000, 1, 1), today)
    payouts = _money(BonusPayout.objects.filter(employee=emp))
    earnings = {
        "sale_points": sale_points,
        "accrued_points": accrued,
        "bonus_payouts": payouts,
        "total": sale_points + accrued + payouts,
        "month_points": (
            approved.filter(date__range=[month_start, month_end]).aggregate(
                t=Coalesce(Sum("points"), Value(ZERO), output_field=_MONEY))["t"]
            + incentives_service.accrued_points(emp, month_start, month_end)
        ),
        "pending_accruals": incentives_service.pending_accruals(emp),
    }

    # ---- target vs actual (this month) -----------------------------------
    target_total = _money(EmployeeTarget.objects.filter(employee=emp), "target_value")
    target = {
        "monthly": target_total,
        "achieved": business["sales_month"],
        "pct": (round(business["sales_month"] / target_total * 100, 1)
                if target_total else None),
    }

    # ---- pipeline ---------------------------------------------------------
    lead_qs = Lead.objects.filter(Lead.team_q(emp))
    lead_total = lead_qs.count()
    won = lead_qs.filter(is_discarded=False, stage=Lead.STAGE_ORDER).count()
    attention_rows, attention_total = lead_service.needs_attention(lead_qs, limit=5)
    pipeline = {
        "total": lead_total,
        "open": lead_qs.filter(is_discarded=False).exclude(stage=Lead.STAGE_ORDER).count(),
        "won": won,
        "lost": lead_qs.filter(is_discarded=True).count(),
        "win_pct": round(won / lead_total * 100, 1) if lead_total else 0.0,
        "funnel": lead_service.funnel(lead_qs),
        "needs_attention": attention_rows,
        "needs_attention_total": attention_total,
    }

    # ---- activity ---------------------------------------------------------
    task_qs = Task.objects.filter(assigned_to=emp, is_deleted=False)
    activity = {
        "tasks_open": task_qs.filter(status__in=Task.OPEN_STATUSES).count(),
        "tasks_overdue": task_qs.filter(
            status__in=Task.OPEN_STATUSES, due_date__lt=today).count(),
        "tasks_done_month": task_qs.filter(
            status=Task.STATUS_COMPLETED,
            completed_at__date__range=[month_start, month_end]).count(),
        "calls_month": calls_service.outcome_breakdown(month_start, month_end, employee_id=emp.id),
    }
    activity["calls_month_total"] = sum(r["count"] for r in activity["calls_month"])

    # ---- clients ----------------------------------------------------------
    client_qs = Client.objects.filter(mapped_to=emp)
    client_ids = list(client_qs.values_list("id", flat=True))
    top_clients = list(
        client_qs.annotate(
            transacted=Coalesce(
                Sum("sales__amount", filter=Q(sales__status=Sale.STATUS_APPROVED)),
                Value(ZERO), output_field=_MONEY),
        ).filter(transacted__gt=0).order_by("-transacted")[:5]
    )
    clients = {
        "count": len(client_ids),
        "transacted": (
            _money(Sale.objects.filter(client_id__in=client_ids, status=Sale.STATUS_APPROVED))
            + _money(Renewal.objects.filter(client_id__in=client_ids), "premium_amount")
        ) if client_ids else ZERO,
        "top": top_clients,
    }

    # ---- 12-month trend ---------------------------------------------------
    trend_start = (month_start.replace(year=month_start.year - 1)
                   if month_start.month == 12 else
                   date(month_start.year - 1, month_start.month, 1))
    sales_by_month = {
        r["m"]: r["amount"] for r in
        approved.filter(date__gte=trend_start)
        .annotate(m=TruncMonth("date")).values("m").order_by()
        .annotate(amount=Sum("amount"))
    }
    renew_by_month = {
        r["m"]: r["amount"] for r in
        renewals.filter(premium_collected_on__gte=trend_start)
        .annotate(m=TruncMonth("premium_collected_on")).values("m").order_by()
        .annotate(amount=Sum("premium_amount"))
    }
    trend = []
    y, m = trend_start.year, trend_start.month
    while (y, m) <= (today.year, today.month):
        key = date(y, m, 1)
        s = sales_by_month.get(key) or ZERO
        r = renew_by_month.get(key) or ZERO
        trend.append({"month": key, "sales": s, "renewals": r, "total": s + r})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    peak = max((t["total"] for t in trend), default=ZERO) or Decimal("1")
    for t in trend:
        t["pct"] = round(t["total"] / peak * 100, 1)

    return {
        "business": business, "mix": mix, "earnings": earnings, "target": target,
        "pipeline": pipeline, "activity": activity, "clients": clients,
        "trend": trend, "fy_label": f"FY {fy_start.year}-{str(fy_end.year)[2:]}",
        "month_label": today.strftime("%B %Y"),
    }


def list_stats(employees):
    """Per-employee headline figures for the team list, in four queries.

    Built for a card grid, so it must never be a per-row lookup.
    """
    ids = [e.id for e in employees]
    if not ids:
        return {}
    today = date.today()
    month_start, month_end = month_bounds(today)

    sales = {
        r["employee_id"]: r["amount"] for r in
        Sale.objects.filter(employee_id__in=ids, status=Sale.STATUS_APPROVED,
                            date__range=[month_start, month_end])
        .values("employee_id").order_by().annotate(amount=Sum("amount"))
    }
    targets = {
        r["employee_id"]: r["t"] for r in
        EmployeeTarget.objects.filter(employee_id__in=ids)
        .values("employee_id").order_by().annotate(t=Sum("target_value"))
    }
    open_tasks = {
        r["assigned_to_id"]: r["n"] for r in
        Task.objects.filter(assigned_to_id__in=ids, is_deleted=False,
                            status__in=Task.OPEN_STATUSES)
        .values("assigned_to_id").order_by().annotate(n=Count("id"))
    }
    overdue_tasks = {
        r["assigned_to_id"]: r["n"] for r in
        Task.objects.filter(assigned_to_id__in=ids, is_deleted=False,
                            status__in=Task.OPEN_STATUSES, due_date__lt=today)
        .values("assigned_to_id").order_by().annotate(n=Count("id"))
    }

    out = {}
    for eid in ids:
        amount = sales.get(eid) or ZERO
        target = targets.get(eid) or ZERO
        out[eid] = {
            "month_sales": amount,
            "target": target,
            "pct": round(amount / target * 100, 1) if target else None,
            "open_tasks": open_tasks.get(eid, 0),
            "overdue_tasks": overdue_tasks.get(eid, 0),
        }
    return out

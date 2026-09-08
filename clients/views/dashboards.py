"""Dashboard views: admin, employee, management, performance, net business/SIP."""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from calendar import monthrange, month_name
from itertools import cycle

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.utils.timezone import now
from django.db.models import Sum, Q, Count
from django.db.models.functions import TruncDay, TruncMonth, TruncYear
from django.core.exceptions import FieldError
from django.db import transaction

from .. import permissions
from ..templatetags.custom_filters import inr
from ..models import (
    Client,
    Lead,
    Sale,
    Product,
    ProductMarginSlab,
    Employee,
    Target,
    EmployeeTarget,
    MonthlyTargetHistory,
    CalendarEvent,
    NetBusinessEntry,
    NetSipEntry,
    Notification,
    ManagerAccessConfig,
    FirmSettings,
    Campaign,
    CampaignProduct,
)
from ..forms import (
    EmployeeCreateForm,
    EmployeeDeactivateForm,
    FirmSettingsForm,
)
from ..services import incentives as incentives_service
from ..services import leads as lead_service
from .helpers import get_manager_access, category_name_map, _lead_queryset_for_request


def _kyc_missing_count(user):
    """Missing-PAN count for the dashboard banner (scoped like the KYC page)."""
    from .kyc import missing_pan_count_for
    return missing_pan_count_for(user)
from ..services.targets import (
    target_employees,
    working_days_in_month,
    baseline_monthly_map,
    employee_target_map,
    resolve_monthly_target,
    resolve_daily_target,
)


def _ordered_product_names(extra_names=None):
    # Categories only: sales are folded into their parent by `category_name_map`
    # before they are looked up here, so a sub-product column is always empty.
    names = list(Product.objects.main().in_display_order().values_list("name", flat=True))
    seen = set(names)
    for item in (extra_names or []):
        cleaned = (item or "").strip()
        if cleaned and cleaned not in seen:
            names.append(cleaned)
            seen.add(cleaned)
    return names


# ── Sub-product roll-up for the dashboards ────────────────────────────────────
# The dashboards show one row per top-level product CATEGORY. A sub-product's
# sales and targets fold into its parent so the boards don't pile up dozens of
# sub-product rows; a sub-product sold on its own still counts, just under its
# category total.

# canonical definition lives in helpers so the reports share it
_category_name_map = category_name_map


def _rollup_product_names(names, cat_map):
    """Collapse a product-name list to top-level buckets, order-preserving."""
    out, seen = [], set()
    for n in names:
        c = cat_map.get(n, n)
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _sum_by_product(qs, field, cat_map, by_emp=False):
    """Sum `field` grouped by dashboard bucket (sub-products fold into their
    category). Returns {bucket: total} or {(emp_id, bucket): total}."""
    group = ["employee_id", "product"] if by_emp else ["product"]
    out = {}
    for r in qs.values(*group).annotate(total=Sum(field)):
        c = cat_map.get(r["product"], r["product"])
        key = (r["employee_id"], c) if by_emp else c
        out[key] = out.get(key, Decimal("0")) + (r["total"] or Decimal("0"))
    return out


def _category_members(cat_map):
    """{category -> [its sub-product names]} for summing sub-product targets."""
    members = {}
    for name, cat in cat_map.items():
        members.setdefault(cat, []).append(name)
    return members


def _normalize_product_code(raw_code):
    cleaned = (raw_code or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not cleaned:
        return ""
    normalized = ""
    for ch in cleaned:
        if ch.isalnum() or ch == "_":
            normalized += ch
    return normalized[:30]


def _parse_margin(raw, default=Decimal("0.00")):
    """Parse a percentage field, clamped to a sensible non-negative value."""
    if raw in (None, ""):
        return default
    try:
        val = Decimal(str(raw).strip())
    except Exception:
        return default
    if val < 0:
        return Decimal("0.00")
    return val


def _parse_amount(raw, default=None):
    if raw in (None, ""):
        return default
    try:
        val = Decimal(str(raw).strip())
    except Exception:
        return default
    return val if val >= 0 else default


def _is_admin_user(user):
    return permissions.is_admin(user)


# Category → chart colour for the product-mix donut. Validated colourblind-safe
# palette (see the dashboard redesign proposal); unknown categories fall to grey.
_PRODUCT_COLORS = {
    "Life Insurance": "#2563D6", "SIP": "#C68A1E", "Health Insurance": "#7C3AED",
    "Lumsum": "#0E8A6E", "PMS": "#C2417E", "Motor Insurance": "#E8790F",
}


def _pct_delta(cur, prev):
    """Percent change cur vs prev, rounded int, or None when prev is 0."""
    if not prev:
        return None
    return int(round((cur - prev) / prev * 100))


def _elapsed_working_days(year, month, today):
    """Weekdays (Mon–Fri) from the 1st through `today` inclusive."""
    last = today.day if (today.year == year and today.month == month) else monthrange(year, month)[1]
    return sum(1 for d in range(1, last + 1) if date(year, month, d).weekday() < 5) or 1


def _renewal_attention(today, employee=None):
    """Renewal follow-up counts for approved Health/Life policies, mirroring the
    renewal_reminders cron. Returns {"c30","prem30","c5","c7"}. Scope to one
    employee (as seller or the client's mapped owner) when given.
    ponytail: iterates the approved insurance book; annotate in SQL if it slows."""
    qs = (
        Sale.objects.filter(status=Sale.STATUS_APPROVED)
        .filter(
            Q(product_ref__code__in=["HEALTH_INS", "LIFE_INS"])
            | Q(product__iexact="Health Insurance")
            | Q(product__iexact="Life Insurance")
        )
        .select_related("product_ref")
    )
    if employee is not None:
        qs = qs.filter(Q(employee=employee) | Q(client__mapped_to=employee))
    c30, c5, prem30 = 0, 0, Decimal("0")
    for sale in qs:
        nxt = sale.next_renewal_date(today)
        if not nxt:
            continue
        days = (nxt - today).days
        if 0 <= days <= 30:
            c30 += 1
            prem30 += sale.annual_premium or Decimal("0")
            if days <= 5:
                c5 += 1
    from ..services import sales as sales_service
    c7 = len(sales_service.renewal_due_sale_ids(today, employee=employee))
    return {"c30": c30, "prem30": prem30, "c5": c5, "c7": c7}


def _emis_due_this_month(today, employee=None):
    """Count of multiyear-EMI health policies whose EMI schedule covers this
    month (the emi_reminders cron would call these clients). Scope to one
    employee (seller or the client's mapped owner) when given."""
    from ..services import sales as sales_service
    return len(sales_service.emi_due_sale_ids(today, employee=employee))


_PRODUCT_DISPLAY = {"Lumsum": "Lumpsum"}


def _stacked_category_trend(base_qs, today, months=6):
    """6-month trend for `base_qs` (approved sales), each month split by product
    CATEGORY (sub-products fold into their parent). Returns
    {prods:[{name,color}], data:[{label, seg:[amount per prod]}], total, best}."""
    cat_map = _category_name_map()
    y, m, seq = today.year, today.month, []
    for _ in range(months):
        seq.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    seq.reverse()

    # One group-by over the whole window, bucketed in Python — this used to be
    # one query per month, and the page calls it more than once.
    window_start = date(seq[0][0], seq[0][1], 1)
    by_month, cats = {}, {}
    rows = (
        base_qs.filter(date__gte=window_start)
        .annotate(mstart=TruncMonth("date"))
        .values("mstart", "product")
        .annotate(t=Sum("amount"))
    )
    for r in rows:
        key = (r["mstart"].year, r["mstart"].month)
        c = cat_map.get(r["product"], r["product"])
        agg = by_month.setdefault(key, {})
        agg[c] = agg.get(c, Decimal("0")) + (r["t"] or Decimal("0"))
        cats[c] = True

    per_month = [
        (date(yy, mm, 1).strftime("%b"), by_month.get((yy, mm), {}))
        for (yy, mm) in seq
    ]

    order = list(Product.objects.filter(name__in=list(cats)).order_by("display_order", "name").values_list("name", flat=True))
    for c in cats:
        if c not in order:
            order.append(c)
    prods = [{"name": _PRODUCT_DISPLAY.get(c, c), "color": _PRODUCT_COLORS.get(c, "#9B8B6A")} for c in order]
    data = [{"label": lbl, "seg": [float(agg.get(c, Decimal("0"))) for c in order]} for (lbl, agg) in per_month]

    totals = [sum(row["seg"]) for row in data]
    grand = sum(totals)
    best_i = max(range(len(totals)), key=lambda i: totals[i]) if totals else None
    return {
        "prods": prods, "data": data,
        "total": grand, "avg": (grand / len(data)) if data else 0.0,
        "best_label": data[best_i]["label"] if best_i is not None else "",
        "best_amount": totals[best_i] if best_i is not None else 0.0,
        "mom": _pct_delta(totals[-1], totals[-2]) if len(totals) >= 2 else None,
    }


def _profile_gap(request):
    """Data for the dashboard's "complete your profile" nudge, or None.

    Cheap: request.user.employee is already loaded by this point and
    missing_fields() is pure Python over that instance.
    """
    emp = getattr(request.user, "employee", None)
    if emp is None:
        return None
    missing = emp.missing_fields()
    if not missing:
        return None
    return {"percent": emp.profile_completeness, "missing": missing}


@login_required
def admin_dashboard(request):
    emp = getattr(request.user, "employee", None)
    if not permissions.is_admin_or_manager(request.user):
        return redirect("clients:employee_dashboard")
    today = timezone.now().date()
    month = today.month
    year = today.year

    admin_emp = getattr(request.user, "employee", None)

    product_labels = {
        "Lumsum": "Lumpsum",
    }

    def _build_breakup(data_map):
        return [
            {
                "product": product,
                "label": product_labels.get(product, product),
                "value": data_map.get(product, Decimal("0")),
            }
            for product in products
        ]

    now_ts = timezone.now()
    today_date = now_ts.date()
    # The agenda widget (one common calendar) loads its items client-side
    # from dashboard_agenda_json; only the employee filter needs context.
    all_employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username")
    pipeline_rows, pipeline_total = lead_service.needs_attention(_lead_queryset_for_request(request))

    all_sales_qs = Sale.objects.all()
    monthly_sales_qs = Sale.objects.filter(status=Sale.STATUS_APPROVED, created_at__year=year, created_at__month=month)
    approved_sales_all = Sale.objects.filter(status=Sale.STATUS_APPROVED)
    cat_map = _category_name_map()
    products = _rollup_product_names(
        _ordered_product_names(
            list(all_sales_qs.exclude(product="").values_list("product", flat=True).distinct())
            + list(Target.objects.exclude(product="").values_list("product", flat=True).distinct())
        ),
        cat_map,
    )
    cat_members = _category_members(cat_map)

    total_clients = Client.objects.count()
    total_sales = monthly_sales_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    sales_points = monthly_sales_qs.aggregate(total=Sum("points"))["total"] or Decimal("0")
    # Earlier-year credits from multiyear policies are earned this month but are
    # NOT this month's selling. Kept as their own figure all the way to the
    # template: folded into one number they make the month look like it sold
    # business it did not.
    accrued_rows = incentives_service.accrued_rows(
        None, date(year, month, 1), date(year, month, monthrange(year, month)[1]))
    accrued_points = sum((a.points for a in accrued_rows), Decimal("0"))
    total_points = sales_points + accrued_points
    total_salary_all = Employee.objects.aggregate(total=Sum("salary"))["total"] or Decimal("0")
    admin_points_scale = max(total_points, total_salary_all, Decimal("1"))
    admin_salary_ratio = (total_salary_all / admin_points_scale) * Decimal("100") if admin_points_scale else Decimal("0")
    admin_points_ratio = (total_points / admin_points_scale) * Decimal("100") if admin_points_scale else Decimal("0")
    admin_extra_points = max(total_points - total_salary_all, Decimal("0"))
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])

    admin_self_sales = Decimal("0")
    admin_self_points = Decimal("0")
    admin_self_pending_points = Decimal("0")
    admin_self_points_map = {}
    admin_self_sales_map = {}
    if admin_emp:
        self_sales_qs = Sale.objects.filter(employee=admin_emp, status=Sale.STATUS_APPROVED, date__year=year, date__month=month)
        admin_self_sales = self_sales_qs.aggregate(total=Sum("amount"))['total'] or Decimal("0")
        admin_self_points = self_sales_qs.aggregate(total=Sum("points"))['total'] or Decimal("0")
        admin_self_pending_points = Sale.objects.filter(employee=admin_emp, status=Sale.STATUS_PENDING, date__year=year, date__month=month).aggregate(total=Sum("points"))['total'] or Decimal("0")

        admin_self_points_map = _sum_by_product(self_sales_qs, "points", cat_map)
        admin_self_sales_map = _sum_by_product(self_sales_qs, "amount", cat_map)

    overall_points_map = _sum_by_product(monthly_sales_qs, "points", cat_map)
    overall_sales_map = _sum_by_product(monthly_sales_qs, "amount", cat_map)

    admin_self_points_breakup = _build_breakup(admin_self_points_map)
    admin_overall_points_breakup = _build_breakup(overall_points_map)
    admin_self_sales_breakup = _build_breakup(admin_self_sales_map)
    admin_overall_sales_breakup = _build_breakup(overall_sales_map)
    sip_sales = overall_sales_map.get("SIP", Decimal("0"))
    lumsum_sales = overall_sales_map.get("Lumsum", Decimal("0"))
    life_sales = overall_sales_map.get("Life Insurance", Decimal("0"))
    health_sales = overall_sales_map.get("Health Insurance", Decimal("0"))
    motor_sales = overall_sales_map.get("Motor Insurance", Decimal("0"))
    pms_sales = overall_sales_map.get("PMS", Decimal("0"))

    # Per-employee targets: each employee may carry a different monthly target
    # by competency/level (EmployeeTarget); otherwise fall back to the product
    # baseline (Target). Daily targets are derived as monthly / working days.
    working_days = working_days_in_month(year, month)
    baseline_map = baseline_monthly_map()
    emp_map = employee_target_map()
    target_employee_ids = list(target_employees().values_list("id", flat=True))

    # A category's target is the sum of its own target and its sub-products'.
    def _cat_daily_target(eid, cat):
        return sum(
            (resolve_daily_target(eid, n, working_days, emp_map=emp_map, baseline_map=baseline_map)
             for n in [cat] + cat_members.get(cat, [])),
            Decimal("0"),
        )

    def _cat_monthly_target(eid, cat):
        return sum(
            (resolve_monthly_target(eid, n, emp_map=emp_map, baseline_map=baseline_map)
             for n in [cat] + cat_members.get(cat, [])),
            Decimal("0"),
        )

    admin_daily_targets_display = []
    admin_monthly_targets_display = []
    if admin_emp:
        admin_today_map = _sum_by_product(
            monthly_sales_qs.filter(employee=admin_emp, date=today), "amount", cat_map)
        admin_month_map = _sum_by_product(
            monthly_sales_qs.filter(employee=admin_emp), "amount", cat_map)

        for product in products:
            target_val = _cat_daily_target(admin_emp.id, product)
            achieved = admin_today_map.get(product, Decimal("0"))
            progress = (achieved / target_val * 100) if target_val else 0
            admin_daily_targets_display.append({
                "product": product,
                "target_value": target_val,
                "achieved": achieved,
                "progress": progress,
            })

        for product in products:
            target_val = _cat_monthly_target(admin_emp.id, product)
            achieved = admin_month_map.get(product, Decimal("0"))
            progress = (achieved / target_val * 100) if target_val else 0
            admin_monthly_targets_display.append({
                "product": product,
                "target_value": target_val,
                "achieved": achieved,
                "progress": progress,
            })

    # ── Pre-aggregate once instead of per-product / per-employee loops.
    # This collapses 4 N×M N+1 loops (≥120 queries) into 4 group-by queries.
    today_by_product = _sum_by_product(approved_sales_all.filter(date=today), "amount", cat_map)
    month_by_product = _sum_by_product(monthly_sales_qs, "amount", cat_map)
    today_by_emp_product = _sum_by_product(
        approved_sales_all.filter(date=today), "amount", cat_map, by_emp=True)
    month_by_emp_product = _sum_by_product(monthly_sales_qs, "amount", cat_map, by_emp=True)

    # Org-wide targets are the sum of each active employee's resolved target
    # (per-head model), not a single baseline multiplied by headcount.
    overall_daily_progress = []
    for product in products:
        target_value = sum(
            (_cat_daily_target(eid, product) for eid in target_employee_ids),
            Decimal("0"),
        )
        achieved = today_by_product.get(product, Decimal("0"))
        progress = (achieved / target_value * 100) if target_value else 0
        overall_daily_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

    overall_monthly_progress = []
    for product in products:
        achieved = month_by_product.get(product, Decimal("0"))
        target_value = sum(
            (_cat_monthly_target(eid, product) for eid in target_employee_ids),
            Decimal("0"),
        )
        progress = (achieved / target_value * 100) if target_value else 0
        overall_monthly_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

    employees = Employee.objects.select_related("user").filter(active=True)

    daily_employee_product = []
    for emp_obj in employees:
        emp_entry = {
            "employee": emp_obj.user.username if hasattr(emp_obj, "user") else emp_obj.name,
            "products": []
        }
        for product in products:
            achieved = today_by_emp_product.get((emp_obj.id, product), Decimal("0"))
            target = _cat_daily_target(emp_obj.id, product)
            progress = (achieved / target * 100) if target else 0
            emp_entry["products"].append({
                "product": product,
                "achieved": achieved,
                "target": target,
                "progress": progress,
            })
        daily_employee_product.append(emp_entry)

    monthly_employee_product = []
    for emp_obj in employees:
        emp_entry = {
            "employee": emp_obj.user.username if hasattr(emp_obj, "user") else emp_obj.name,
            "products": []
        }
        for product in products:
            achieved = month_by_emp_product.get((emp_obj.id, product), Decimal("0"))
            target = _cat_monthly_target(emp_obj.id, product)
            progress = (achieved / target * 100) if target else 0
            emp_entry["products"].append({
                "product": product,
                "achieved": achieved,
                "target": target,
                "progress": progress,
            })
        monthly_employee_product.append(emp_entry)

    monthly_summary = {
        "total_clients": Client.objects.filter(created_at__year=year, created_at__month=month).count(),
        "total_sales": total_sales,
        "total_points": total_points,
        "sales_points": sales_points,
        "accrued_points": accrued_points,
        "accrued_rows": accrued_rows,
        "sip": sip_sales,
        "lumpsum": lumsum_sales,
        "life": life_sales,
        "health": health_sales,
        "motor": motor_sales,
        "pms": pms_sales,
    }

    # ── Redesigned overview: money, momentum, and what needs doing today.
    # All from data already computed above or existing report/cron helpers.
    from .reports import _month_margin_breakdown, business_overview_data
    from ..models import InsuranceClaim

    prev_last = month_start - timedelta(days=1)
    py, pm = prev_last.year, prev_last.month
    prev_premium = (Sale.objects.filter(status=Sale.STATUS_APPROVED, created_at__year=py, created_at__month=pm)
                    .aggregate(t=Sum("amount"))["t"] or Decimal("0"))
    new_clients = monthly_summary["total_clients"]
    prev_clients = Client.objects.filter(created_at__year=py, created_at__month=pm).count()

    _, margin_totals = _month_margin_breakdown(year, month)
    _, prev_margin_totals = _month_margin_breakdown(py, pm)
    mtd_margin = margin_totals["margin_amount"]

    pending_qs = Sale.objects.filter(status=Sale.STATUS_PENDING)
    pending_count = pending_qs.count()
    oldest_pending = pending_qs.order_by("date").values_list("date", flat=True).first()
    pending_oldest_days = (today - oldest_pending).days if oldest_pending else 0

    _ra = _renewal_attention(today)
    renew_30, renew_30_premium, renew_5 = _ra["c30"], _ra["prem30"], _ra["c5"]
    emis_due = _emis_due_this_month(today)
    open_claims = InsuranceClaim.objects.filter(status__in=InsuranceClaim.OPEN_STATUSES).count()
    claims_action = InsuranceClaim.objects.filter(status=InsuranceClaim.STATUS_INTIMATED).count()
    kyc_missing = _kyc_missing_count(request.user)

    # Firm run-rate: this-month achieved vs the summed per-head targets, with a
    # working-day projection so "are we going to make it?" reads at a glance.
    firm_target = sum((p["target"] for p in overall_monthly_progress), Decimal("0"))
    firm_pct = (total_sales / firm_target * 100) if firm_target else Decimal("0")
    elapsed_wd = _elapsed_working_days(year, month, today)
    firm_projection = (total_sales / elapsed_wd * working_days) if elapsed_wd else total_sales
    pace_pct = min(elapsed_wd / working_days * 100, 100) if working_days else 0

    # Product mix (main categories only) for the donut.
    product_mix = [
        {"name": product_labels.get(name, name), "amount": float(amt),
         "color": _PRODUCT_COLORS.get(name, "#9B8B6A")}
        for name, amt in sorted(overall_sales_map.items(), key=lambda kv: kv[1], reverse=True)
        if amt and amt > 0
    ]

    # Leaderboard: fold the per-employee product grid into one row each, ranked
    # by target attainment; drop employees with neither sales nor a target.
    leaderboard = []
    for row in monthly_employee_product:
        prem = sum((p["achieved"] for p in row["products"]), Decimal("0"))
        tgt = sum((p["target"] for p in row["products"]), Decimal("0"))
        if not prem and not tgt:
            continue
        att = (prem / tgt * 100) if tgt else Decimal("0")
        leaderboard.append({"name": row["employee"], "premium": prem, "target": tgt,
                            "attainment": att, "low": bool(tgt) and att < 80})
    leaderboard.sort(key=lambda r: r["attainment"] if r["target"] else Decimal("-1"), reverse=True)

    # 6-month premium trend (reuses the business-overview series).
    trend = [{"label": t["label"], "year": t["sublabel"], "amount": float(t["amount"])}
             for t in business_overview_data(approved_sales_all, period="month",
                                              columns=6, today=today)["trend"]]
    trend_amounts = [t["amount"] for t in trend]
    trend_total = sum(trend_amounts)
    trend_avg = trend_total / len(trend) if trend else 0.0
    trend_best = max(trend, key=lambda t: t["amount"]) if trend else None
    trend_mom = _pct_delta(trend_amounts[-1], trend_amounts[-2]) if len(trend_amounts) >= 2 else None

    overview = {
        "mtd_premium": total_sales,
        "mtd_premium_delta": _pct_delta(total_sales, prev_premium),
        "mtd_margin": mtd_margin,
        "mtd_margin_delta": _pct_delta(mtd_margin, prev_margin_totals["margin_amount"]),
        "blended_margin_pct": margin_totals["blended_percent"],
        "new_clients": new_clients,
        "new_clients_delta": (new_clients - prev_clients),
        "pending_count": pending_count,
        "pending_oldest_days": pending_oldest_days,
        "renew_30": renew_30, "renew_30_premium": renew_30_premium, "renew_5": renew_5,
        "emis_due": emis_due,
        "open_claims": open_claims, "claims_action": claims_action,
        "kyc_missing": kyc_missing,
        "firm_target": firm_target, "firm_achieved": total_sales,
        "firm_pct": firm_pct, "firm_projection": firm_projection,
        "firm_on_track": firm_projection >= firm_target if firm_target else True,
        "working_days": working_days, "elapsed_wd": elapsed_wd, "pace_pct": pace_pct,
        "product_mix": product_mix,
        "mix_total": sum((m["amount"] for m in product_mix), 0.0),
        "leaderboard": leaderboard,
        "trend": trend,
        "trend_max": max((t["amount"] for t in trend), default=0.0),
        "trend_total": trend_total,
        "trend_avg": trend_avg,
        "trend_best": trend_best,
        "trend_mom": trend_mom,
    }

    notifications = []
    unread_notifications = 0
    if request.user.is_authenticated:
        notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:10]
        unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False).count()

    context = {
        "total_clients": total_clients,
        "total_sales": total_sales,
        "total_points": total_points,
        "sales_points": sales_points,
        "accrued_points": accrued_points,
        "accrued_rows": accrued_rows,
        "total_salary_all": total_salary_all,
        "admin_salary_ratio": admin_salary_ratio,
        "admin_points_ratio": admin_points_ratio,
        "admin_extra_points": admin_extra_points,
        "admin_self_sales": admin_self_sales,
        "admin_self_points": admin_self_points,
        "admin_self_pending_points": admin_self_pending_points,
        "admin_self_points_breakup": admin_self_points_breakup,
        "admin_overall_points_breakup": admin_overall_points_breakup,
        "admin_self_sales_breakup": admin_self_sales_breakup,
        "admin_overall_sales_breakup": admin_overall_sales_breakup,
        "admin_daily_targets": admin_daily_targets_display,
        "admin_monthly_targets": admin_monthly_targets_display,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "overall_daily_progress": overall_daily_progress,
        "overall_monthly_progress": overall_monthly_progress,
        "daily_employee_product": daily_employee_product,
        "monthly_employee_product": monthly_employee_product,
        "monthly_summary": monthly_summary,
        "notifications": notifications,
        "unread_notifications": unread_notifications,
        "today_date": today_date,
        "is_admin_dashboard": True,
        "all_employees": all_employees,
        # Live-pipeline leads with nothing scheduled — the deals the agenda
        # cannot show, because nobody dated them.
        "pipeline_rows": pipeline_rows,
        "pipeline_total": pipeline_total,
        "month_start": month_start,
        "month_end": month_end,
        "kyc_missing_count": _kyc_missing_count(request.user),
        "overview": overview,
        # Managers see this same team-oversight page, relabelled "Team Dashboard".
        "is_pure_admin": permissions.is_admin(request.user),
    }

    context["profile_gap"] = _profile_gap(request)
    return render(request, "dashboards/admin_dashboard.html", context)


@login_required
def employee_management(request):
    if not permissions.is_admin(request.user):
        return HttpResponseForbidden("Admins only.")

    create_form = EmployeeCreateForm()
    manager_access = ManagerAccessConfig.current()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            create_form = EmployeeCreateForm(request.POST)
            if create_form.is_valid():
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=create_form.cleaned_data["username"],
                        email=create_form.cleaned_data.get("email"),
                        password=create_form.cleaned_data["password"],
                        is_active=True,
                    )
                    Employee.objects.create(
                        user=user,
                        role=create_form.cleaned_data["role"],
                        salary=create_form.cleaned_data["salary"],
                        active=True,
                    )
                messages.success(request, "Employee created successfully.")
                return redirect("clients:employee_management")
            else:
                error_text = "; ".join([
                    "; ".join([str(msg) for msg in errs]) for errs in create_form.errors.values()
                ])
                messages.error(request, error_text or "Please correct the errors in the form.")
        elif action == "update_manager_access":
            bool_fields = [
                "allow_view_all_sales",
                "allow_approve_sales",
                "allow_edit_sales",
                "allow_manage_incentives",
                "allow_recalc_points",
                "allow_client_analysis",
                "allow_employee_performance",
                "allow_lead_management",
                "allow_calling_admin",
                "allow_business_tracking",
            ]
            for field in bool_fields:
                setattr(manager_access, field, field in request.POST)
            manager_access.save(update_fields=bool_fields + ["updated_at"])
            messages.success(request, "Manager rights updated.")
            return redirect("clients:employee_management")
        elif action in ("deactivate", "activate"):
            status_form = EmployeeDeactivateForm(request.POST)
            if status_form.is_valid():
                emp_obj = get_object_or_404(Employee, pk=status_form.cleaned_data["employee_id"])
                if action == "deactivate":
                    if not emp_obj.active:
                        messages.info(request, "Employee is already inactive.")
                        return redirect("clients:employee_management")

                    other_emps = list(Employee.objects.filter(active=True).exclude(id=emp_obj.id))
                    mapped_clients = list(Client.objects.filter(mapped_to=emp_obj))

                    if mapped_clients and not other_emps:
                        messages.error(request, "Cannot deactivate the last active employee while they have mapped clients. Reassign or add another employee first.")
                        return redirect("clients:employee_management")

                    if other_emps:
                        rr = cycle(other_emps)
                        for client in mapped_clients:
                            new_emp = next(rr)
                            client.reassign_to(new_emp, changed_by=request.user, note="Auto-reassigned on deactivation")

                    with transaction.atomic():
                        emp_obj.active = False
                        emp_obj.save(update_fields=["active"])
                        if emp_obj.user_id:
                            emp_obj.user.is_active = False
                            emp_obj.user.save(update_fields=["is_active"])

                    messages.success(request, f"Deactivated {emp_obj.user.username} and reassigned {len(mapped_clients)} clients evenly.")
                    return redirect("clients:employee_management")

                if emp_obj.active:
                    messages.info(request, "Employee is already active.")
                    return redirect("clients:employee_management")

                with transaction.atomic():
                    emp_obj.active = True
                    emp_obj.save(update_fields=["active"])
                    if emp_obj.user_id:
                        emp_obj.user.is_active = True
                        emp_obj.user.save(update_fields=["is_active"])

                messages.success(request, f"Reactivated {emp_obj.user.username}.")
                return redirect("clients:employee_management")
            else:
                messages.error(request, "Invalid employee action request.")

    employees = Employee.objects.select_related("user").all().order_by("-active", "user__username")
    context = {
        "employees": employees,
        "create_form": create_form,
        "manager_access": manager_access,
    }
    return render(request, "employees/manage.html", context)


@login_required
def employee_dashboard(request):
    emp = request.user.employee
    today = now().date()
    role = getattr(emp, "role", "")
    is_manager = role == "manager"
    is_admin = permissions.is_admin(request.user)
    manager_access = get_manager_access() if is_manager else None
    allow_company_sections = permissions.can(request.user, "employee_performance")
    month_start = date(today.year, today.month, 1)
    month_end = date(today.year, today.month, monthrange(today.year, today.month)[1])

    now_ts = timezone.now()
    today_date = now_ts.date()
    # Agenda widget items come from dashboard_agenda_json (one common calendar).
    # The pipeline panel is separate on purpose: it lists the leads with
    # *nothing* dated, which is exactly what a calendar cannot show.
    pipeline_rows, pipeline_total = lead_service.needs_attention(
        Lead.objects.select_related("assigned_to__user").filter(Lead.team_q(emp)))

    cat_map = _category_name_map()
    products = _rollup_product_names(
        _ordered_product_names(
            list(
                Sale.objects.filter(employee=emp)
                .exclude(product="")
                .values_list("product", flat=True)
                .distinct()
            )
            + list(Target.objects.exclude(product="").values_list("product", flat=True).distinct())
        ),
        cat_map,
    )
    cat_members = _category_members(cat_map)

    monthly_sales_approved = Sale.objects.filter(
        employee=emp,
        status=Sale.STATUS_APPROVED,
        date__year=today.year,
        date__month=today.month
    )
    monthly_sales_pending = Sale.objects.filter(
        employee=emp,
        status=Sale.STATUS_PENDING,
        date__year=today.year,
        date__month=today.month
    )
    today_sales_qs = monthly_sales_approved.filter(date=today)

    total_sales = monthly_sales_approved.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    sales_points = monthly_sales_approved.aggregate(total=Sum("points"))["total"] or Decimal("0")
    # Multiyear health policies pay their 2nd/3rd year on the anniversary, with
    # no sale row behind it — those points are earned this month too, but they
    # are not this month's selling and are shown as their own line.
    accrued_rows = incentives_service.accrued_rows(
        emp, date(today.year, today.month, 1), today.replace(day=monthrange(today.year, today.month)[1]))
    accrued_points = sum((a.points for a in accrued_rows), Decimal("0"))
    total_points = sales_points + accrued_points
    pending_points = monthly_sales_pending.aggregate(total=Sum("points"))["total"] or Decimal("0")
    salary_points = getattr(emp, "salary", Decimal("0")) or Decimal("0")
    if not isinstance(salary_points, Decimal):
        salary_points = Decimal(str(salary_points))
    points_scale = max(total_points, salary_points, Decimal("1"))
    salary_ratio = (salary_points / points_scale) * Decimal("100") if points_scale else Decimal("0")
    points_ratio = (total_points / points_scale) * Decimal("100") if points_scale else Decimal("0")
    extra_points = max(total_points - salary_points, Decimal("0"))

    product_points_map = _sum_by_product(monthly_sales_approved, "points", cat_map)

    product_labels = {
        "Lumsum": "Lumpsum",
    }
    product_point_breakup = [
        {
            "product": product,
            "label": product_labels.get(product, product),
            "points": product_points_map.get(product, Decimal("0")),
        }
        for product in products
    ]

    today_sales_dict = _sum_by_product(today_sales_qs, "amount", cat_map)
    month_sales_dict = _sum_by_product(monthly_sales_approved, "amount", cat_map)
    sip_sales = month_sales_dict.get("SIP", Decimal("0"))
    lumsum_sales = month_sales_dict.get("Lumsum", Decimal("0"))
    life_sales = month_sales_dict.get("Life Insurance", Decimal("0"))
    health_sales = month_sales_dict.get("Health Insurance", Decimal("0"))
    motor_sales = month_sales_dict.get("Motor Insurance", Decimal("0"))
    pms_sales = month_sales_dict.get("PMS", Decimal("0"))
    product_sales_breakup = [
        {
            "product": product,
            "label": product_labels.get(product, product),
            "amount": month_sales_dict.get(product, Decimal("0")),
        }
        for product in products
    ]

    # Per-employee targets resolved against this employee's competency-based
    # EmployeeTarget rows, falling back to the product baseline.
    working_days = working_days_in_month(today.year, today.month)
    baseline_map = baseline_monthly_map()
    emp_map = employee_target_map()

    # A category's target sums its own target and its sub-products'.
    def _cat_daily_target(eid, cat):
        return sum(
            (resolve_daily_target(eid, n, working_days, emp_map=emp_map, baseline_map=baseline_map)
             for n in [cat] + cat_members.get(cat, [])),
            Decimal("0"),
        )

    def _cat_monthly_target(eid, cat):
        return sum(
            (resolve_monthly_target(eid, n, emp_map=emp_map, baseline_map=baseline_map)
             for n in [cat] + cat_members.get(cat, [])),
            Decimal("0"),
        )

    daily_targets_display = []
    for product in products:
        target_value = _cat_daily_target(emp.id, product)
        achieved = today_sales_dict.get(product, Decimal("0"))
        progress = (achieved / target_value * 100) if target_value else 0
        daily_targets_display.append({
            "product": product,
            "target_value": target_value,
            "achieved": achieved,
            "progress": progress,
        })

    monthly_targets_display = []
    for product in products:
        target_value = _cat_monthly_target(emp.id, product)
        achieved = month_sales_dict.get(product, Decimal("0"))
        progress = (achieved / target_value * 100) if target_value else 0
        monthly_targets_display.append({
            "product": product,
            "target_value": target_value,
            "achieved": achieved,
            "progress": progress,
        })

    history = MonthlyTargetHistory.objects.filter(employee=emp).order_by("-year", "-month")[:6]

    todays_events = CalendarEvent.objects.filter(
        employee=request.user.employee,
        scheduled_time__date=today,
        status="pending",
    ).order_by("scheduled_time")

    # Company-wide aggregates for managers/admins
    overall_daily_progress = []
    overall_monthly_progress = []
    daily_employee_product = []
    monthly_employee_product = []
    overall_product_point_breakup = []
    overall_product_sales_breakup = []

    if allow_company_sections:
        approved_sales_all = Sale.objects.filter(status=Sale.STATUS_APPROVED)
        monthly_sales_qs = approved_sales_all.filter(date__year=today.year, date__month=today.month)
        target_employee_ids = list(target_employees().values_list("id", flat=True))

        # Four grouped totals, one query each — the loops below used to run a
        # separate aggregate per product and per employee×product (~110 queries).
        today_sales_all = approved_sales_all.filter(date=today)
        day_by_product = _sum_by_product(today_sales_all, "amount", cat_map)
        month_by_product = _sum_by_product(monthly_sales_qs, "amount", cat_map)
        day_by_emp = _sum_by_product(today_sales_all, "amount", cat_map, by_emp=True)
        month_by_emp = _sum_by_product(monthly_sales_qs, "amount", cat_map, by_emp=True)

        for product in products:
            # Per-head model: org target = sum of each active employee's target.
            target_value = sum(
                (_cat_daily_target(eid, product) for eid in target_employee_ids),
                Decimal("0"),
            )
            achieved = day_by_product.get(product, Decimal("0"))
            progress = (achieved / target_value * 100) if target_value else 0
            overall_daily_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

        for product in products:
            achieved = month_by_product.get(product, Decimal("0"))
            target_value = sum(
                (_cat_monthly_target(eid, product) for eid in target_employee_ids),
                Decimal("0"),
            )
            progress = (achieved / target_value * 100) if target_value else 0
            overall_monthly_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

        employees_all = Employee.objects.select_related("user").filter(active=True)
        for e in employees_all:
            emp_entry_daily = {
                "employee": e.user.username if hasattr(e, "user") else getattr(e, "name", ""),
                "products": [],
            }
            for product in products:
                achieved = day_by_emp.get((e.id, product), Decimal("0"))
                target = _cat_daily_target(e.id, product)
                progress = (achieved / target * 100) if target else 0
                emp_entry_daily["products"].append({
                    "product": product,
                    "achieved": achieved,
                    "target": target,
                    "progress": progress,
                })
            daily_employee_product.append(emp_entry_daily)

        for e in employees_all:
            emp_entry_monthly = {
                "employee": e.user.username if hasattr(e, "user") else getattr(e, "name", ""),
                "products": [],
            }
            for product in products:
                achieved = month_by_emp.get((e.id, product), Decimal("0"))
                target = _cat_monthly_target(e.id, product)
                progress = (achieved / target * 100) if target else 0
                emp_entry_monthly["products"].append({
                    "product": product,
                    "achieved": achieved,
                    "target": target,
                    "progress": progress,
                })
            monthly_employee_product.append(emp_entry_monthly)

        overall_points_map = _sum_by_product(monthly_sales_qs, "points", cat_map)
        overall_sales_map = _sum_by_product(monthly_sales_qs, "amount", cat_map)

        overall_product_point_breakup = [
            {
                "product": product,
                "label": product_labels.get(product, product),
                "points": overall_points_map.get(product, Decimal("0")),
            }
            for product in products
        ]

        overall_product_sales_breakup = [
            {
                "product": product,
                "label": product_labels.get(product, product),
                "amount": overall_sales_map.get(product, Decimal("0")),
            }
            for product in products
        ]

    # ── Gamified Campaign Challenges ──
    # For every campaign currently live, show this employee's progress on each
    # campaign product: boosted-rate earnings (unit) or slab-target progress.
    campaign_challenges = []
    active_campaigns = (
        Campaign.objects.filter(is_active=True, start_date__lte=today, end_date__gte=today)
        .prefetch_related("products__slabs", "products__product_ref")
        .order_by("end_date")
    )
    for camp in active_campaigns:
        days_left = (camp.end_date - today).days
        for cp in camp.products.all():
            win_sales = Sale.objects.filter(
                employee=emp,
                product_ref=cp.product_ref,
                date__range=[camp.start_date, camp.end_date],
            )
            approved_win = win_sales.filter(status=Sale.STATUS_APPROVED)
            cumulative = approved_win.aggregate(t=Sum("amount"))["t"] or Decimal("0")
            earned = approved_win.aggregate(t=Sum("points"))["t"] or Decimal("0")
            pending_pts = win_sales.filter(status=Sale.STATUS_PENDING).aggregate(t=Sum("points"))["t"] or Decimal("0")

            challenge = {
                "campaign": camp.name,
                "end_date": camp.end_date,
                "days_left": days_left,
                "product": cp.product_ref.name,
                "benefit_type": cp.benefit_type,
                "earned_points": earned,
                "pending_points": pending_pts,
                "cumulative": cumulative,
            }

            if cp.benefit_type == CampaignProduct.BENEFIT_UNIT:
                challenge["unit_amount"] = cp.unit_amount or Decimal("0")
                challenge["points_per_unit"] = cp.points_per_unit or Decimal("0")
            else:
                slabs = sorted(cp.slabs.all(), key=lambda s: s.threshold)
                current_payout = Decimal("0")
                next_threshold = None
                next_payout = None
                for s in slabs:
                    if cumulative >= s.threshold:
                        current_payout = s.payout
                    else:
                        next_threshold = s.threshold
                        next_payout = s.payout
                        break
                if next_threshold is not None and next_threshold > 0:
                    progress = min(cumulative / next_threshold * Decimal("100"), Decimal("100"))
                    amount_to_next = next_threshold - cumulative
                    top_reached = False
                else:
                    progress = Decimal("100")
                    amount_to_next = Decimal("0")
                    top_reached = True
                challenge.update({
                    "current_payout": current_payout,
                    "next_threshold": next_threshold,
                    "next_payout": next_payout,
                    "progress": progress,
                    "amount_to_next": amount_to_next,
                    "top_reached": top_reached,
                    "max_payout": slabs[-1].payout if slabs else Decimal("0"),
                })
            campaign_challenges.append(challenge)

    # ── Redesigned personal overview: my day, am-I-on-track by product, trend ──
    from ..models import Task

    prev_last = month_start - timedelta(days=1)
    prev_sales = (Sale.objects.filter(employee=emp, status=Sale.STATUS_APPROVED,
                                       date__year=prev_last.year, date__month=prev_last.month)
                  .aggregate(t=Sum("amount"))["t"] or Decimal("0"))
    my_target_total = sum((t["target_value"] for t in monthly_targets_display), Decimal("0"))
    my_attainment = (total_sales / my_target_total * 100) if my_target_total else Decimal("0")

    elapsed_wd = _elapsed_working_days(today.year, today.month, today)
    pace_pct = min(elapsed_wd / working_days * 100, 100) if working_days else 0

    # Per-product target status (pace-aware) so the rep sees where they lag.
    track_rows = []
    for t in monthly_targets_display:
        tgt, ach = t["target_value"], t["achieved"]
        if not tgt:
            continue
        att = ach / tgt * 100
        status = "ahead" if att >= 100 else ("ontrack" if att >= pace_pct else "behind")
        track_rows.append({
            "product": _PRODUCT_DISPLAY.get(t["product"], t["product"]),
            "achieved": ach, "target": tgt, "att": att,
            "gap": max(tgt - ach, Decimal("0")), "status": status,
            "color": _PRODUCT_COLORS.get(t["product"], "#9B8B6A"),
        })
    track_rows.sort(key=lambda r: (r["status"] != "behind", r["att"]))
    behind = [r for r in track_rows if r["status"] == "behind"]

    my_mix = [
        {"name": _PRODUCT_DISPLAY.get(k, k), "amount": float(v),
         "color": _PRODUCT_COLORS.get(k, "#9B8B6A")}
        for k, v in sorted(month_sales_dict.items(), key=lambda kv: kv[1], reverse=True)
        if v and v > 0
    ]

    my_trend = _stacked_category_trend(
        Sale.objects.filter(employee=emp, status=Sale.STATUS_APPROVED), today)

    _ra = _renewal_attention(today, employee=emp)
    emp_overview = {
        "my_sales": total_sales, "my_sales_delta": _pct_delta(total_sales, prev_sales),
        "attainment": my_attainment, "target_total": my_target_total,
        "points": total_points, "extra_points": extra_points,
        # Split out so the KPI can say how much of the month's points came from
        # earlier years rather than from selling this month.
        "sales_points": sales_points, "accrued_points": accrued_points,
        "pending_count": monthly_sales_pending.count(),
        # Due today OR already overdue. Keying this on due_date == today read as
        # zero on a day when the only open work was late, which is exactly when
        # someone needs to see it.
        "tasks_due": Task.objects.filter(assigned_to=emp, due_date__lte=today,
                                         status__in=Task.OPEN_STATUSES, is_deleted=False).count(),
        "tasks_overdue": Task.objects.filter(assigned_to=emp, due_date__lt=today,
                                             status__in=Task.OPEN_STATUSES, is_deleted=False).count(),
        "events_today": todays_events.count(),
        "renewals_7": _ra["c7"], "renewals_7_premium": _ra["prem30"],
        "emis_due": _emis_due_this_month(today, employee=emp),
        "elapsed_wd": elapsed_wd, "working_days": working_days, "pace_pct": pace_pct,
        "track_rows": track_rows, "behind": behind,
        "my_mix": my_mix, "my_mix_total": sum((m["amount"] for m in my_mix), 0.0),
        "trend": my_trend,
    }

    context = {
        "pipeline_rows": pipeline_rows,
        "pipeline_total": pipeline_total,
        "total_sales": total_sales,
        "emp_overview": emp_overview,
        "total_points": total_points,
        "sales_points": sales_points,
        "accrued_points": accrued_points,
        "accrued_rows": accrued_rows,
        "salary_points": salary_points,
        "salary_ratio": salary_ratio,
        "points_ratio": points_ratio,
        "extra_points": extra_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "todays_events": todays_events,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "today_sales_dict": today_sales_dict,
        "month_sales_dict": month_sales_dict,
        "daily_targets": daily_targets_display,
        "monthly_targets": monthly_targets_display,
        "history": history,
        "today_date": today_date,
        "is_admin_dashboard": False,
        "pending_points": pending_points,
        "product_point_breakup": product_point_breakup,
        "product_sales_breakup": product_sales_breakup,
        "overall_product_point_breakup": overall_product_point_breakup,
        "overall_product_sales_breakup": overall_product_sales_breakup,
        "show_company_sections": allow_company_sections,
        "overall_daily_progress": overall_daily_progress,
        "overall_monthly_progress": overall_monthly_progress,
        "monthly_employee_product": monthly_employee_product,
        "is_manager": is_manager,
        "is_admin": is_admin,
        "month_start": month_start,
        "month_end": month_end,
        "now_ts": now_ts,
        "campaign_challenges": campaign_challenges,
        "kyc_missing_count": _kyc_missing_count(request.user),
    }
    context["profile_gap"] = _profile_gap(request)
    return render(request, "dashboards/employee_dashboard.html", context)


@login_required
def firm_settings_page(request):
    admin_emp = getattr(request.user, "employee", None)
    if not permissions.is_admin(request.user):
        return HttpResponseForbidden("Admins only.")

    settings_obj = FirmSettings.get_settings()

    if request.method == "POST":
        form = FirmSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Firm settings updated successfully.")
            return redirect("clients:firm_settings")
        messages.error(request, "Please correct the errors below.")
    else:
        form = FirmSettingsForm(instance=settings_obj)

    return render(
        request,
        "settings/firm_settings.html",
        {
            "form": form,
            "settings_obj": settings_obj,
        },
    )


@login_required
def product_management_page(request):
    admin_emp = getattr(request.user, "employee", None)
    if not permissions.is_admin(request.user):
        return HttpResponseForbidden("Admins only.")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        def _domain_from_renewal_choice(default_domain=Product.DOMAIN_SALE):
            renewal_raw = request.POST.get("renewal_tracked")
            renewal_tracked = (renewal_raw or "").strip().lower()
            if renewal_tracked in {"yes", "no"}:
                return Product.DOMAIN_BOTH if renewal_tracked == "yes" else Product.DOMAIN_SALE

            # Backward compatibility with old form payloads using explicit domain only.
            posted_domain = (request.POST.get("domain") or "").strip()
            if posted_domain in {Product.DOMAIN_SALE, Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH}:
                if posted_domain == Product.DOMAIN_BOTH:
                    return Product.DOMAIN_BOTH
                if posted_domain == Product.DOMAIN_RENEWAL:
                    return Product.DOMAIN_BOTH
                return Product.DOMAIN_SALE
            return default_domain

        def _resolve_parent(self_pk=None):
            """Category chosen for a (sub-)product. Rejects self-reference and
            nesting deeper than one level; returns (parent_or_None, error)."""
            raw = (request.POST.get("parent_id") or "").strip()
            if not raw:
                return None, None
            parent = Product.objects.filter(pk=raw).first()
            if not parent:
                return None, "Selected category does not exist."
            if self_pk and parent.pk == self_pk:
                return None, "A product can't be its own category."
            if parent.parent_id:
                return None, "Categories can't be nested — pick a top-level product."
            if self_pk and Product.objects.filter(parent_id=self_pk).exists():
                return None, "This product already has sub-products, so it can't sit under a category."
            return parent, None

        if action == "add":
            name = (request.POST.get("name") or "").strip()
            domain = _domain_from_renewal_choice(default_domain=Product.DOMAIN_SALE)
            code = _normalize_product_code(request.POST.get("code"))
            display_order = request.POST.get("display_order")

            if not name:
                messages.error(request, "Product name is required.")
                return redirect("clients:product_management")

            try:
                display_order_val = int(display_order) if display_order not in (None, "") else 0
            except ValueError:
                display_order_val = 0

            if not code:
                code = _normalize_product_code(name)

            if Product.objects.filter(name__iexact=name).exists():
                messages.error(request, f"Product '{name}' already exists.")
                return redirect("clients:product_management")

            if Product.objects.filter(code=code).exists():
                messages.error(request, f"Product code '{code}' already exists.")
                return redirect("clients:product_management")

            parent, parent_err = _resolve_parent()
            if parent_err:
                messages.error(request, parent_err)
                return redirect("clients:product_management")

            Product.objects.create(
                name=name,
                code=code,
                parent=parent,
                domain=domain,
                display_order=display_order_val,
                margin_percent=_parse_margin(request.POST.get("margin_percent")),
                renewal_margin_percent=_parse_margin(request.POST.get("renewal_margin_percent")),
                show_in_reports=bool(request.POST.get("show_in_reports")),
                is_active=True,
            )
            messages.success(request, f"Product '{name}' added.")
            return redirect("clients:product_management")

        if action == "bulk_add":
            names = request.POST.getlist("bulk_name")
            parents = request.POST.getlist("bulk_parent_id")
            margins = request.POST.getlist("bulk_margin")
            # Categories a row may sit under (top-level products only).
            categories = {str(p.id): p for p in Product.objects.filter(parent__isnull=True)}
            taken_names = {n.strip().lower() for n in Product.objects.values_list("name", flat=True)}
            taken_codes = set(Product.objects.values_list("code", flat=True))

            added, errors = 0, []
            for i, raw_name in enumerate(names):
                name = (raw_name or "").strip()
                if not name:
                    continue  # skip blank rows
                if name.lower() in taken_names:
                    errors.append(f"'{name}' already exists — skipped.")
                    continue

                parent_id = (parents[i] if i < len(parents) else "").strip()
                parent = None
                if parent_id:
                    parent = categories.get(parent_id)
                    if parent is None:
                        errors.append(f"'{name}': invalid category — skipped.")
                        continue

                code = _normalize_product_code(name) or "PROD"
                base, n = code, 1
                while code in taken_codes:
                    n += 1
                    code = f"{base[:26]}_{n}"

                Product.objects.create(
                    name=name, code=code, parent=parent,
                    domain=Product.DOMAIN_SALE,
                    margin_percent=_parse_margin(margins[i] if i < len(margins) else None),
                    is_active=True,
                )
                taken_names.add(name.lower())
                taken_codes.add(code)
                added += 1

            if added:
                messages.success(request, f"{added} product(s) added.")
            for err in errors:
                messages.error(request, err)
            if not added and not errors:
                messages.error(request, "Nothing to add — enter at least one product name.")
            return redirect("clients:product_management")

        if action == "update":
            product_id = request.POST.get("product_id")
            product = get_object_or_404(Product, pk=product_id)
            name = (request.POST.get("name") or "").strip()
            domain = _domain_from_renewal_choice(
                default_domain=Product.DOMAIN_BOTH if product.domain in {Product.DOMAIN_BOTH, Product.DOMAIN_RENEWAL} else Product.DOMAIN_SALE
            )
            code = _normalize_product_code(request.POST.get("code"))
            display_order = request.POST.get("display_order")

            if not name:
                messages.error(request, "Product name is required.")
                return redirect("clients:product_management")

            try:
                display_order_val = int(display_order) if display_order not in (None, "") else product.display_order
            except ValueError:
                display_order_val = product.display_order

            if not code:
                code = _normalize_product_code(name)

            if Product.objects.exclude(pk=product.pk).filter(name__iexact=name).exists():
                messages.error(request, f"Product '{name}' already exists.")
                return redirect("clients:product_management")

            if Product.objects.exclude(pk=product.pk).filter(code=code).exists():
                messages.error(request, f"Product code '{code}' already exists.")
                return redirect("clients:product_management")

            parent, parent_err = _resolve_parent(self_pk=product.pk)
            if parent_err:
                messages.error(request, parent_err)
                return redirect("clients:product_management")

            product.name = name
            product.code = code
            product.parent = parent
            product.domain = domain
            product.display_order = display_order_val
            product.margin_percent = _parse_margin(request.POST.get("margin_percent"), product.margin_percent)
            product.renewal_margin_percent = _parse_margin(
                request.POST.get("renewal_margin_percent"), product.renewal_margin_percent
            )
            product.show_in_reports = bool(request.POST.get("show_in_reports"))
            product.save(update_fields=[
                "name", "code", "parent", "domain", "display_order",
                "margin_percent", "renewal_margin_percent", "show_in_reports",
                "updated_at",
            ])
            messages.success(request, f"Product '{name}' updated.")
            return redirect("clients:product_management")

        if action == "archive":
            product_id = request.POST.get("product_id")
            reason = (request.POST.get("reason") or "").strip()
            product = get_object_or_404(Product, pk=product_id)
            product.archive(reason=reason)
            messages.success(request, f"Product '{product.name}' archived.")
            return redirect("clients:product_management")

        if action == "restore":
            product_id = request.POST.get("product_id")
            product = get_object_or_404(Product, pk=product_id)
            product.is_active = True
            product.archived_at = None
            product.archived_reason = ""
            product.save(update_fields=["is_active", "archived_at", "archived_reason", "updated_at"])
            messages.success(request, f"Product '{product.name}' restored.")
            return redirect("clients:product_management")

        if action == "add_margin_slab":
            product = get_object_or_404(Product, pk=request.POST.get("product_id"))
            policy_type = (request.POST.get("policy_type") or "").strip()
            if not product.is_health:
                policy_type = ""
            elif policy_type not in {"fresh", "port"}:
                messages.error(request, "Select Fresh or Port for Health Insurance margin slabs.")
                return redirect("clients:product_management")

            min_amount = _parse_amount(request.POST.get("min_amount"), default=None)
            max_amount = _parse_amount(request.POST.get("max_amount"), default=None)
            margin_percent = _parse_margin(request.POST.get("margin_percent"), default=None)

            if min_amount is None or margin_percent is None:
                messages.error(request, "Min amount and margin % are required for a slab.")
                return redirect("clients:product_management")
            if max_amount is not None and max_amount < min_amount:
                messages.error(request, "Max amount cannot be less than min amount.")
                return redirect("clients:product_management")

            overlap = ProductMarginSlab.objects.filter(
                product=product, policy_type=policy_type
            ).filter(
                Q(max_amount__isnull=True) | Q(max_amount__gte=min_amount)
            ).filter(
                Q(min_amount__lte=max_amount) if max_amount is not None else Q()
            )
            if overlap.exists():
                messages.error(request, "This revenue range overlaps an existing slab for the same type.")
                return redirect("clients:product_management")

            ProductMarginSlab.objects.create(
                product=product,
                policy_type=policy_type,
                min_amount=min_amount,
                max_amount=max_amount,
                margin_percent=margin_percent,
            )
            messages.success(request, f"Margin slab added for '{product.name}'.")
            return redirect("clients:product_management")

        if action == "delete_margin_slab":
            slab = get_object_or_404(ProductMarginSlab, pk=request.POST.get("slab_id"))
            pname = slab.product.name
            slab.delete()
            messages.success(request, f"Margin slab removed from '{pname}'.")
            return redirect("clients:product_management")

        if action == "toggle_mdrt":
            fs = FirmSettings.get_settings()
            this_year = FirmSettings.current_mdrt_year()
            if fs.is_mdrt_active():
                fs.mdrt_active_year = None
                messages.success(request, "MDRT turned off — Advisor rates now apply to new sales.")
            else:
                fs.mdrt_active_year = this_year
                messages.success(request, f"MDRT active for {this_year} — MDRT rates apply to new sales until 1 January.")
            fs.save(update_fields=["mdrt_active_year", "updated_at"])
            return redirect("clients:product_management")

        if action == "set_active_life_plans":
            # Bulk "tick what you sell": the checked plans become active, the
            # rest inactive. Scoped to Life Insurance plans so it never touches
            # other products.
            parent = Product.objects.filter(code="LIFE_INS").first()
            if parent:
                checked = {int(i) for i in request.POST.getlist("plan_id") if i.isdigit()}
                for plan in parent.children.all():
                    active = plan.id in checked
                    if plan.is_active != active or (active and plan.archived_at):
                        plan.is_active = active
                        if active:
                            plan.archived_at = None
                            plan.archived_reason = ""
                        plan.save(update_fields=["is_active", "archived_at",
                                                 "archived_reason", "updated_at"])
                messages.success(request, f"{len(checked)} life-insurance plan(s) marked active.")
            return redirect("clients:product_management")

        messages.error(request, "Unsupported action.")
        return redirect("clients:product_management")

    life_parent = Product.objects.filter(code="LIFE_INS").first()
    life_plan_ids = set(
        Product.objects.filter(parent__code="LIFE_INS").values_list("id", flat=True)
    )
    # Life plans get their own picker below; keep them out of the main table.
    products = (
        Product.objects.exclude(id__in=life_plan_ids)
        .order_by("display_order", "name")
        .select_related("parent")
        .prefetch_related("margin_slabs")
    )
    life_plans = (
        Product.objects.filter(parent__code="LIFE_INS").order_by("name")
        if life_parent else []
    )
    # Categories a (sub-)product may sit under: top-level products only.
    categories = [p for p in products if not p.parent_id]
    return render(
        request,
        "settings/product_management.html",
        {
            "products": products,
            "categories": categories,
            "life_plans": life_plans,
            "life_active_count": sum(1 for p in life_plans if p.is_active),
            "mdrt_active": FirmSettings.get_settings().is_mdrt_active(),
            "mdrt_year": FirmSettings.current_mdrt_year(),
        },
    )


@login_required
def target_management(request):
    """Admin page to set each employee's personal monthly target per product.

    Targets are personal (not a shared pool split across the team). The page
    shows, per product, the sum of every active employee's monthly target and
    how much each employee has achieved this month.
    """
    admin_emp = getattr(request.user, "employee", None)
    if not permissions.is_admin(request.user):
        return HttpResponseForbidden("Admins only.")

    employees = list(target_employees())
    # Targets are set on main products only; sub-product sales roll up into the
    # parent category's target (below), so sub-products get no column here.
    active_products = list(Product.objects.filter(is_active=True, parent__isnull=True).order_by("display_order", "name"))

    if request.method == "POST":
        emp_by_id = {e.id: e for e in employees}
        prod_by_id = {p.id: p for p in active_products}
        saved = 0
        cleared = 0
        for key, raw in request.POST.items():
            if not key.startswith("tv-"):
                continue
            try:
                _, emp_id, prod_id = key.split("-")
                emp_id, prod_id = int(emp_id), int(prod_id)
            except (ValueError, TypeError):
                continue
            emp = emp_by_id.get(emp_id)
            product = prod_by_id.get(prod_id)
            if not emp or not product:
                continue

            value = _parse_amount(raw, default=None)
            if value is None or value <= 0:
                # Blank / zero clears the personal target (falls back to baseline).
                deleted, _ = EmployeeTarget.objects.filter(employee=emp, product=product.name).delete()
                if deleted:
                    cleared += 1
                continue

            EmployeeTarget.objects.update_or_create(
                employee=emp,
                product=product.name,
                defaults={"target_value": value, "product_ref": product},
            )
            saved += 1

        parts = []
        if saved:
            parts.append(f"{saved} target(s) saved")
        if cleared:
            parts.append(f"{cleared} cleared")
        messages.success(request, ", ".join(parts) + "." if parts else "No changes.")
        return redirect("clients:target_management")

    # ── Build the grid (rows = employees, columns = products) ──────────────
    today = timezone.now().date()
    year, month = today.year, today.month

    emp_map = employee_target_map(employees)

    month_sales = (
        Sale.objects.filter(status=Sale.STATUS_APPROVED, date__year=year, date__month=month)
        .values("employee_id", "product")
        .annotate(total=Sum("amount"))
    )
    # Roll a sub-product sale into its parent category so it counts toward the
    # main product's target (which is the only level we set targets at).
    cat_map = _category_name_map()
    achieved_map = {}
    for r in month_sales:
        pname = cat_map.get(r["product"], r["product"])
        key = (r["employee_id"], pname)
        achieved_map[key] = achieved_map.get(key, Decimal("0")) + (r["total"] or Decimal("0"))

    product_names = [p.name for p in active_products]
    col_target_totals = {p.name: Decimal("0") for p in active_products}
    col_achieved_totals = {p.name: Decimal("0") for p in active_products}

    rows = []
    for emp in employees:
        cells = []
        row_target_total = Decimal("0")
        row_achieved_total = Decimal("0")
        for product in active_products:
            pname = product.name
            explicit = emp_map.get((emp.id, pname))  # None if not set
            # Targets are per-employee only; a blank cell means no target (0).
            effective = explicit if explicit is not None else Decimal("0")
            achieved = achieved_map.get((emp.id, pname), Decimal("0"))
            progress = (achieved / effective * 100) if effective else 0

            cells.append({
                "product_id": product.id,
                "product": pname,
                "input_value": explicit if explicit is not None else "",
                "is_baseline": False,
                "baseline": Decimal("0"),
                "effective": effective,
                "achieved": achieved,
                "progress": progress,
            })
            col_target_totals[pname] += effective
            col_achieved_totals[pname] += achieved
            row_target_total += effective
            row_achieved_total += achieved

        rows.append({
            "employee": emp,
            "name": emp.user.username if hasattr(emp, "user") else getattr(emp, "name", ""),
            "cells": cells,
            "target_total": row_target_total,
            "achieved_total": row_achieved_total,
            "progress": (row_achieved_total / row_target_total * 100) if row_target_total else 0,
        })

    column_totals = []
    grand_target = Decimal("0")
    grand_achieved = Decimal("0")
    for product in active_products:
        tt = col_target_totals[product.name]
        at = col_achieved_totals[product.name]
        column_totals.append({
            "product": product.name,
            "target_total": tt,
            "achieved_total": at,
            "progress": (at / tt * 100) if tt else 0,
        })
        grand_target += tt
        grand_achieved += at

    # Where the month itself stands — a 60% attainment on the 5th and on the
    # 28th are opposite stories, so the page shows the pace, not just the total.
    days_in_month = monthrange(year, month)[1]
    month_elapsed = Decimal(today.day) / Decimal(days_in_month)
    expected_to_date = grand_target * month_elapsed
    grand_progress = (grand_achieved / grand_target * 100) if grand_target else 0
    untargeted = sum(1 for r in rows if not r["target_total"])

    return render(
        request,
        "settings/target_management.html",
        {
            "crumbs": [{"label": "Settings"}, {"label": "Target Management"}],
            "kpis": [
                {"label": "Team Target", "value": f"₹{inr(grand_target)}", "color": "#4338CA",
                 "sub": f"{len(employees)} employee(s) · {len(active_products)} product(s)"},
                {"label": "Achieved", "value": f"₹{inr(grand_achieved)}", "color": "#15803D",
                 "sub": f"{grand_progress:.0f}% of target"},
                {"label": "Expected by today", "value": f"₹{inr(expected_to_date)}", "color": "#0F766E",
                 "sub": f"day {today.day} of {days_in_month}"},
                {"label": "On pace", "value": "Yes" if grand_achieved >= expected_to_date else "Behind",
                 "color": "#15803D" if grand_achieved >= expected_to_date else "#B45309",
                 "sub": f"₹{inr(abs(grand_achieved - expected_to_date))} {'ahead' if grand_achieved >= expected_to_date else 'short'}"},
                {"label": "No target set", "value": untargeted, "color": "#BE123C" if untargeted else "#64748B",
                 "sub": "employees with a blank row"},
            ],
            "products": active_products,
            "product_names": product_names,
            "rows": rows,
            "column_totals": column_totals,
            "grand_target": grand_target,
            "grand_achieved": grand_achieved,
            "grand_progress": grand_progress,
            "expected_to_date": expected_to_date,
            "days_in_month": days_in_month,
            "day_of_month": today.day,
            "month_label": today.strftime("%B %Y"),
            "employee_count": len(employees),
        },
    )


@login_required
def employee_performance(request):
    """Employee performance overview (MVP)."""
    emp_id = request.GET.get('employee_id')
    is_manager = False
    if hasattr(request.user, 'employee') and request.user.employee.role in ('admin', 'manager'):
        is_manager = True
    if request.user.is_superuser:
        is_manager = True

    if emp_id and is_manager:
        employee = get_object_or_404(Employee, id=emp_id)
    elif hasattr(request.user, 'employee'):
        employee = request.user.employee
    else:
        messages.error(request, 'No employee selected and you are not mapped to an employee.')
        return redirect('clients:admin_dashboard')

    try:
        start_str = request.GET.get('start')
        end_str = request.GET.get('end')
        if start_str:
            start = datetime.fromisoformat(start_str).date()
        else:
            start = date.today() - timedelta(days=30)
        if end_str:
            end = datetime.fromisoformat(end_str).date()
        else:
            end = date.today()
    except Exception:
        start = date.today() - timedelta(days=30)
        end = date.today()

    sales_qs = Sale.objects.filter(employee=employee, date__range=(start, end))
    total_sales = sales_qs.count()
    try:
        total_amount = sales_qs.aggregate(total=Sum('amount'))['total'] or 0
    except FieldError:
        try:
            total_amount = sales_qs.aggregate(total=Sum('total_amount'))['total'] or 0
        except FieldError:
            total_amount = 0
    points = sales_qs.aggregate(total=Sum('points'))['total'] or 0

    counts_by_day = {
        row['date']: row['cnt']
        for row in sales_qs.values('date').annotate(cnt=Count('id'))
    }
    days = []
    sales_series = []
    current = start
    while current <= end:
        days.append(current.strftime('%Y-%m-%d'))
        sales_series.append(counts_by_day.get(current, 0))
        current += timedelta(days=1)

    recent_sales = sales_qs.order_by('-date')[:10]

    if request.GET.get('export') == 'csv':
        import csv as _csv

        resp = HttpResponse(content_type='text/csv')
        filename = f"employee_{employee.id}_performance_{start}_{end}.csv"
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = _csv.writer(resp)
        writer.writerow(['date', 'client', 'amount', 'points', 'product'])
        for s in sales_qs.select_related('client').order_by('date'):
            client_name = s.client.name if getattr(s, 'client', None) else ''
            amount = getattr(s, 'total_amount', None) or getattr(s, 'amount', None) or ''
            points_v = getattr(s, 'points', '')
            product = getattr(s, 'product', '')
            writer.writerow([s.date, client_name, amount, points_v, product])
        return resp

    context = {
        'employee': employee,
        'start': start,
        'end': end,
        'total_sales': total_sales,
        'total_amount': total_amount,
        'points': points,
        'days': days,
        'sales_series': sales_series,
        'recent_sales': recent_sales,
        'is_manager': is_manager,
    }

    if is_manager:
        context['employees'] = Employee.objects.select_related('user').all()

    return render(request, 'sales/employee_performance.html', context)


@login_required
def net_business(request):
    """Net business dashboard: shows sales minus redemptions/SIP stoppage."""
    if not permissions.is_admin_or_manager(request.user):
        messages.error(request, 'You do not have permission to view Net Business.')
        return redirect('clients:admin_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action', 'add')
        editable_entries = NetBusinessEntry.objects.all() if _is_admin_user(request.user) else NetBusinessEntry.objects.filter(created_by=request.user)
        try:
            if action == 'bulk_delete':
                selected_ids = [int(v) for v in request.POST.getlist('selected_ids') if str(v).strip()]
                if not selected_ids:
                    raise ValueError('Select at least one entry to delete')
                deleted_count, _ = editable_entries.filter(id__in=selected_ids).delete()
                if deleted_count == 0:
                    raise ValueError('No matching entries found or permission denied')
                messages.success(request, f'Deleted {deleted_count} entries')
                return redirect('clients:net_business')

            if action == 'delete':
                entry_id = int(request.POST.get('entry_id'))
                if not entry_id:
                    raise ValueError('Missing entry id')
                deleted_count, _ = editable_entries.filter(id=entry_id).delete()
                if deleted_count == 0:
                    raise ValueError('Entry not found or permission denied')
                messages.success(request, 'Entry deleted')
                return redirect('clients:net_business')

            entry_type = request.POST.get('entry_type')
            amount = float(request.POST.get('amount'))
            month_val = int(request.POST.get('month'))
            year_val = int(request.POST.get('year'))
            note = request.POST.get('note', '')

            if entry_type not in ('sale', 'redemption'):
                raise ValueError('Choose Sale or Redemption')
            if month_val < 1 or month_val > 12:
                raise ValueError('Month must be 1-12')

            entry_date = date(year_val, month_val, 1)

            if action == 'update':
                entry_id = int(request.POST.get('entry_id'))
                if not entry_id:
                    raise ValueError('Missing entry id')
                entry = editable_entries.get(id=entry_id)
                entry.entry_type = entry_type
                entry.amount = amount
                entry.date = entry_date
                entry.note = note
                entry.save(update_fields=['entry_type', 'amount', 'date', 'note'])
                messages.success(request, 'Entry updated')
            else:
                NetBusinessEntry.objects.create(
                    entry_type=entry_type,
                    amount=amount,
                    date=entry_date,
                    note=note,
                    created_by=request.user,
                )
                messages.success(request, 'Entry added')
            return redirect('clients:net_business')
        except Exception as e:
            messages.error(request, f'Invalid input: {e}')

    try:
        year_picker_raw = request.GET.get('year_picker')
        selected_year = int(year_picker_raw) if year_picker_raw else date.today().year
    except Exception:
        selected_year = date.today().year

    gran = request.GET.get('granularity', 'month')
    series_mode = request.GET.get('series_mode', 'net')

    current_year = date.today().year
    entry_years = list(NetBusinessEntry.objects.values_list('date__year', flat=True).distinct())
    year_options = sorted(set(list(range(current_year + 1, current_year - 5, -1)) + entry_years), reverse=True)

    if gran == 'year':
        max_year = max(entry_years + [current_year]) if entry_years else current_year
        min_year = max_year - 4
        start = date(min_year, 1, 1)
        end = date(max_year, 12, 31)
    else:
        start = date(selected_year, 1, 1)
        end = date(selected_year, 12, 31)

    try:
        table_year = int(request.GET.get('table_year', selected_year))
    except Exception:
        table_year = selected_year

    entries_qs = NetBusinessEntry.objects.filter(date__range=(start, end))

    if gran == 'day':
        entries_grouped = entries_qs.annotate(period=TruncDay('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    elif gran == 'year':
        entries_grouped = entries_qs.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    else:
        entries_grouped = entries_qs.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')

    data = {}
    for r in entries_grouped:
        key = r['period'].date() if hasattr(r['period'], 'date') else r['period']
        if key not in data:
            data[key] = {'sales': 0, 'redemptions': 0}
        if r['entry_type'] == 'sale':
            data[key]['sales'] = float(r['total'] or 0)
        else:
            data[key]['redemptions'] = float(r['total'] or 0)

    period_totals = []
    if gran == 'year':
        min_year = start.year
        max_year = end.year
        for yr in range(min_year, max_year + 1):
            key = date(yr, 1, 1)
            sales_total = data.get(key, {}).get('sales', 0)
            red_total = data.get(key, {}).get('redemptions', 0)
            net_total = sales_total - red_total
            period_totals.append({
                'period': key.isoformat(),
                'label': str(yr),
                'sales': round(sales_total, 2),
                'redemptions': round(red_total, 2),
                'net': round(net_total, 2),
            })
    else:
        periods = [date(start.year, m, 1) for m in range(1, 13)]
        for p in periods:
            sales_total = data.get(p, {}).get('sales', 0)
            red_total = data.get(p, {}).get('redemptions', 0)
            net_total = sales_total - red_total
            label = p.strftime('%b %Y') if gran == 'month' else p.strftime('%d %b %Y')
            period_totals.append({
                'period': p.isoformat(),
                'label': label,
                'sales': round(sales_total, 2),
                'redemptions': round(red_total, 2),
                'net': round(net_total, 2),
            })

    if gran == 'year':
        table_entries = NetBusinessEntry.objects.filter(date__range=(start, end))
        table_grouped = table_entries.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(y, 1, 1): {'sales': 0, 'redemptions': 0} for y in range(start.year, end.year + 1)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'sales': 0, 'redemptions': 0}
            if r['entry_type'] == 'sale':
                table_map[k]['sales'] += float(r['total'] or 0)
            else:
                table_map[k]['redemptions'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['sales'] - val['redemptions']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'sales': val['sales'], 'redemptions': val['redemptions'], 'net': net_val})
    else:
        table_entries = NetBusinessEntry.objects.filter(date__year=table_year)
        table_grouped = table_entries.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(table_year, m, 1): {'sales': 0, 'redemptions': 0} for m in range(1, 13)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'sales': 0, 'redemptions': 0}
            if r['entry_type'] == 'sale':
                table_map[k]['sales'] += float(r['total'] or 0)
            else:
                table_map[k]['redemptions'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%b %Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['sales'] - val['redemptions']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'sales': val['sales'], 'redemptions': val['redemptions'], 'net': net_val})

    entry_list = NetBusinessEntry.objects.order_by('-date', '-created_at')[:200]

    start_str = start.isoformat() if hasattr(start, 'isoformat') else str(start)
    end_str = end.isoformat() if hasattr(end, 'isoformat') else str(end)

    table_len = len(table_rows)
    sales_sum = sum(r.get('sales', 0) for r in table_rows)
    reds_sum = sum(r.get('redemptions', 0) for r in table_rows)
    net_sum = sum(r.get('net', 0) for r in table_rows)

    def _avg(total):
        return total / table_len if table_len else 0

    context = {
        'start': start,
        'end': end,
        'start_str': start_str,
        'end_str': end_str,
        'granularity': gran,
        'series_mode': series_mode,
        'period_totals_json': json.dumps(period_totals),
        'month_table': table_rows,
        'table_year': table_year,
        'year_options': year_options,
        'entry_list': entry_list,
        'table_totals': {
            'sales_sum': sales_sum,
            'reds_sum': reds_sum,
            'net_sum': net_sum,
            'sales_avg': _avg(sales_sum),
            'reds_avg': _avg(reds_sum),
            'net_avg': _avg(net_sum),
        },
    }

    return render(request, 'dashboards/net_business.html', context)


@login_required
def net_sip(request):
    """Net SIP dashboard: SIP fresh minus SIP stopped."""
    if not permissions.is_admin_or_manager(request.user):
        messages.error(request, 'You do not have permission to view Net SIP.')
        return redirect('clients:admin_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action', 'add')
        editable_entries = NetSipEntry.objects.all() if _is_admin_user(request.user) else NetSipEntry.objects.filter(created_by=request.user)
        try:
            if action == 'bulk_delete':
                selected_ids = [int(v) for v in request.POST.getlist('selected_ids') if str(v).strip()]
                if not selected_ids:
                    raise ValueError('Select at least one entry to delete')
                deleted_count, _ = editable_entries.filter(id__in=selected_ids).delete()
                if deleted_count == 0:
                    raise ValueError('No matching entries found or permission denied')
                messages.success(request, f'Deleted {deleted_count} entries')
                return redirect('clients:net_sip')

            if action == 'delete':
                entry_id = int(request.POST.get('entry_id'))
                if not entry_id:
                    raise ValueError('Missing entry id')
                deleted_count, _ = editable_entries.filter(id=entry_id).delete()
                if deleted_count == 0:
                    raise ValueError('Entry not found or permission denied')
                messages.success(request, 'Entry deleted')
                return redirect('clients:net_sip')

            entry_type = request.POST.get('entry_type')
            amount = float(request.POST.get('amount'))
            month_val = int(request.POST.get('month'))
            year_val = int(request.POST.get('year'))
            note = request.POST.get('note', '')

            if entry_type not in ('fresh', 'stopped'):
                raise ValueError('Choose SIP Fresh or SIP Stopped')
            if month_val < 1 or month_val > 12:
                raise ValueError('Month must be 1-12')

            entry_date = date(year_val, month_val, 1)

            if action == 'update':
                entry_id = int(request.POST.get('entry_id'))
                if not entry_id:
                    raise ValueError('Missing entry id')
                entry = editable_entries.get(id=entry_id)
                entry.entry_type = entry_type
                entry.amount = amount
                entry.date = entry_date
                entry.note = note
                entry.save(update_fields=['entry_type', 'amount', 'date', 'note'])
                messages.success(request, 'Entry updated')
            else:
                NetSipEntry.objects.create(
                    entry_type=entry_type,
                    amount=amount,
                    date=entry_date,
                    note=note,
                    created_by=request.user,
                )
                messages.success(request, 'Entry added')
            return redirect('clients:net_sip')
        except Exception as e:
            messages.error(request, f'Invalid input: {e}')

    try:
        year_picker_raw = request.GET.get('year_picker')
        selected_year = int(year_picker_raw) if year_picker_raw else date.today().year
    except Exception:
        selected_year = date.today().year

    gran = request.GET.get('granularity', 'month')
    series_mode = request.GET.get('series_mode', 'net')

    current_year = date.today().year
    entry_years = list(NetSipEntry.objects.values_list('date__year', flat=True).distinct())
    year_options = sorted(set(list(range(current_year + 1, current_year - 5, -1)) + entry_years), reverse=True)

    if gran == 'year':
        max_year = max(entry_years + [current_year]) if entry_years else current_year
        min_year = max_year - 4
        start = date(min_year, 1, 1)
        end = date(max_year, 12, 31)
    else:
        start = date(selected_year, 1, 1)
        end = date(selected_year, 12, 31)

    try:
        table_year = int(request.GET.get('table_year', selected_year))
    except Exception:
        table_year = selected_year

    entries_qs = NetSipEntry.objects.filter(date__range=(start, end))

    if gran == 'day':
        entries_grouped = entries_qs.annotate(period=TruncDay('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    elif gran == 'year':
        entries_grouped = entries_qs.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    else:
        entries_grouped = entries_qs.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')

    data = {}
    for r in entries_grouped:
        key = r['period'].date() if hasattr(r['period'], 'date') else r['period']
        if key not in data:
            data[key] = {'fresh': 0, 'stopped': 0}
        if r['entry_type'] == 'fresh':
            data[key]['fresh'] = float(r['total'] or 0)
        else:
            data[key]['stopped'] = float(r['total'] or 0)

    period_totals = []
    if gran == 'year':
        min_year = start.year
        max_year = end.year
        for yr in range(min_year, max_year + 1):
            key = date(yr, 1, 1)
            fresh_total = data.get(key, {}).get('fresh', 0)
            stopped_total = data.get(key, {}).get('stopped', 0)
            net_total = fresh_total - stopped_total
            period_totals.append({
                'period': key.isoformat(),
                'label': str(yr),
                'fresh': round(fresh_total, 2),
                'stopped': round(stopped_total, 2),
                'net': round(net_total, 2),
            })
    else:
        periods = [date(start.year, m, 1) for m in range(1, 13)]
        for p in periods:
            fresh_total = data.get(p, {}).get('fresh', 0)
            stopped_total = data.get(p, {}).get('stopped', 0)
            net_total = fresh_total - stopped_total
            label = p.strftime('%b %Y') if gran == 'month' else p.strftime('%d %b %Y')
            period_totals.append({
                'period': p.isoformat(),
                'label': label,
                'fresh': round(fresh_total, 2),
                'stopped': round(stopped_total, 2),
                'net': round(net_total, 2),
            })

    if gran == 'year':
        table_entries = NetSipEntry.objects.filter(date__range=(start, end))
        table_grouped = table_entries.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(y, 1, 1): {'fresh': 0, 'stopped': 0} for y in range(start.year, end.year + 1)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'fresh': 0, 'stopped': 0}
            if r['entry_type'] == 'fresh':
                table_map[k]['fresh'] += float(r['total'] or 0)
            else:
                table_map[k]['stopped'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['fresh'] - val['stopped']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'fresh': val['fresh'], 'stopped': val['stopped'], 'net': net_val})
    else:
        table_entries = NetSipEntry.objects.filter(date__year=table_year)
        table_grouped = table_entries.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(table_year, m, 1): {'fresh': 0, 'stopped': 0} for m in range(1, 13)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'fresh': 0, 'stopped': 0}
            if r['entry_type'] == 'fresh':
                table_map[k]['fresh'] += float(r['total'] or 0)
            else:
                table_map[k]['stopped'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%b %Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['fresh'] - val['stopped']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'fresh': val['fresh'], 'stopped': val['stopped'], 'net': net_val})

    entry_list = NetSipEntry.objects.order_by('-date', '-created_at')[:200]

    start_str = start.isoformat() if hasattr(start, 'isoformat') else str(start)
    end_str = end.isoformat() if hasattr(end, 'isoformat') else str(end)

    table_len = len(table_rows)
    fresh_sum = sum(r.get('fresh', 0) for r in table_rows)
    stopped_sum = sum(r.get('stopped', 0) for r in table_rows)
    net_sum = sum(r.get('net', 0) for r in table_rows)

    def _avg(total):
        return total / table_len if table_len else 0

    context = {
        'start': start,
        'end': end,
        'start_str': start_str,
        'end_str': end_str,
        'granularity': gran,
        'series_mode': series_mode,
        'period_totals_json': json.dumps(period_totals),
        'month_table': table_rows,
        'table_year': table_year,
        'year_options': year_options,
        'entry_list': entry_list,
        'table_totals': {
            'fresh_sum': fresh_sum,
            'stopped_sum': stopped_sum,
            'net_sum': net_sum,
            'fresh_avg': _avg(fresh_sum),
            'stopped_avg': _avg(stopped_sum),
            'net_avg': _avg(net_sum),
        },
    }

    return render(request, 'dashboards/net_sip.html', context)

"""Reports views: past performance, monthly business report."""
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from calendar import month_name, monthrange
from urllib.parse import urlencode

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.db.models import Sum, Q, Count, F, ExpressionWrapper, DecimalField

# A multiyear health premium counts, for this month's margin, only as its
# first-year slice (amount / policy_years). policy_years is 1 for ordinary sales,
# so the slice equals the amount for them.
_ANNUAL_SLICE = ExpressionWrapper(
    F("amount") / F("policy_years"),
    output_field=DecimalField(max_digits=16, decimal_places=4),
)
from django.utils.timezone import now

from .. import permissions
from ..models import (
    Sale, Employee, MonthlyTargetHistory, Product, Expense, ExpenseCategory,
    Renewal, Client,
)
from ..services import incentives
from .helpers import get_manager_access, _last_n_months, category_name_map, product_mix, product_totals


# ── Business Overview: period-grouped trend with product bifurcation ──────────
# Shared by the native app (JSON: clients.views.app_api.app_report_summary) and
# the web page (business_overview). Groups approved-sale business into the last
# N periods (month / quarter / half-year / year), split per product so each
# column shows its product-wise compartments, plus a per-employee leaderboard
# with the same split.

PERIODS = ("month", "quarter", "half", "year")
MAX_COLUMNS = 24


def _period_ranges(period, columns, today):
    """Last `columns` calendar periods ending with the one containing `today`.

    Returns a chronological list of (start, end_exclusive, label, sublabel).
    """
    ranges = []
    if period == "year":
        y = today.year
        for _ in range(columns):
            ranges.append((date(y, 1, 1), date(y + 1, 1, 1), str(y), ""))
            y -= 1
    elif period == "half":
        y = today.year
        h = 0 if today.month <= 6 else 1  # 0 = Jan–Jun, 1 = Jul–Dec
        for _ in range(columns):
            if h == 0:
                ranges.append((date(y, 1, 1), date(y, 7, 1), "H1", str(y)))
            else:
                ranges.append((date(y, 7, 1), date(y + 1, 1, 1), "H2", str(y)))
            h -= 1
            if h < 0:
                h, y = 1, y - 1
    elif period == "quarter":
        y = today.year
        q = (today.month - 1) // 3  # 0..3
        for _ in range(columns):
            sm = q * 3 + 1
            end = date(y + 1, 1, 1) if q == 3 else date(y, sm + 3, 1)
            ranges.append((date(y, sm, 1), end, f"Q{q + 1}", str(y)))
            q -= 1
            if q < 0:
                q, y = 3, y - 1
    else:  # month
        y, m = today.year, today.month
        for _ in range(columns):
            end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
            ranges.append((date(y, m, 1), end, date(y, m, 1).strftime("%b"), str(y)))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
    ranges.reverse()
    return ranges


def business_overview_data(base, period="month", columns=6, today=None,
                           with_leaderboard=False, select=0):
    """Compute the period-grouped, product-split business trend.

    `base` is an already-scoped approved-Sale queryset. `select` picks which
    column the mix + leaderboard describe (0 = the latest, 1 = the one before
    it), so a tap on a bar can retell the rest of the page for that period.
    Money values are
    Decimals (callers format/serialize). `buckets` names the product columns,
    aligned index-for-index with each row's `by_product` list; an "Other"
    bucket is appended only if unmapped-product sales exist in the window.
    """
    if today is None:
        today = now().date()
    if period not in PERIODS:
        period = "month"
    try:
        columns = int(columns)
    except (TypeError, ValueError):
        columns = 6
    columns = max(1, min(columns, MAX_COLUMNS))

    ranges = _period_ranges(period, columns, today)

    # One column per top-level product. Sub-products don't get their own
    # column — they point at their parent's index, so every lookup below folds
    # them into the category total.
    buckets = list(
        Product.objects.filter(is_active=True, parent__isnull=True)
        .order_by("display_order", "name")
        .values_list("name", flat=True)
    )
    bucket_index = {name: i for i, name in enumerate(buckets)}
    cat_map = category_name_map()
    for sub, parent in cat_map.items():
        if parent in bucket_index:
            bucket_index[sub] = bucket_index[parent]

    trend = []
    other_used = False
    for (start, end, label, sublabel) in ranges:
        qs = base.filter(date__gte=start, date__lt=end)
        by_product = [Decimal("0")] * len(buckets)
        other = Decimal("0")
        total = Decimal("0")
        count = 0
        for r in qs.values("product").annotate(t=Sum("amount"), n=Count("id")):
            amt = r["t"] or Decimal("0")
            total += amt
            count += r["n"]
            idx = bucket_index.get(r["product"])
            if idx is None:
                other += amt
            else:
                by_product[idx] += amt
        if other > 0:
            other_used = True
        trend.append({"label": label, "sublabel": sublabel, "amount": total,
                      "count": count, "by_product": by_product, "_other": other})

    if other_used:
        buckets = buckets + ["Other"]
    for row in trend:
        if other_used:
            row["by_product"] = row["by_product"] + [row.pop("_other")]
        else:
            row.pop("_other")

    # The selected period (latest by default) drives the mix + leaderboard.
    try:
        select = int(select)
    except (TypeError, ValueError):
        select = 0
    select = min(max(select, 0), len(ranges) - 1)
    cur_start, cur_end, cur_label, cur_sublabel = ranges[-1 - select]
    cur_qs = base.filter(date__gte=cur_start, date__lt=cur_end)
    products = product_mix(cur_qs)

    data = {
        "period": period,
        "columns": columns,
        "buckets": buckets,
        "trend": trend,
        "current_label": cur_label,
        "current_sublabel": cur_sublabel,
        "select": select,
        # Inclusive bounds, so a drill-down link can hand them to the sales list.
        "current_start": cur_start.isoformat(),
        "current_end": (cur_end - timedelta(days=1)).isoformat(),
        "products": products,
    }

    if with_leaderboard:
        other_idx = buckets.index("Other") if "Other" in buckets else None
        emp_rows = {}
        for r in cur_qs.values(
            "employee_id", "employee__user__username",
            "employee__user__first_name", "product",
        ).annotate(t=Sum("amount"), p=Sum("points")):
            eid = r["employee_id"]
            row = emp_rows.get(eid)
            if row is None:
                row = {
                    "employee_id": eid,
                    "name": r["employee__user__first_name"] or r["employee__user__username"],
                    "amount": Decimal("0"), "points": Decimal("0"),
                    "by_product": [Decimal("0")] * len(buckets),
                }
                emp_rows[eid] = row
            amt = r["t"] or Decimal("0")
            row["amount"] += amt
            row["points"] += (r["p"] or Decimal("0"))
            idx = bucket_index.get(r["product"], other_idx)
            if idx is not None:
                row["by_product"][idx] += amt
        data["leaderboard"] = sorted(
            emp_rows.values(), key=lambda x: x["amount"], reverse=True
        )[:15]

    return data


_OVERVIEW_PALETTE = ["#E5B740", "#3b82f6", "#10b981", "#ef4444",
                     "#8b5cf6", "#f97316", "#14b8a6", "#ec4899"]


def _compact_inr(value):
    """Indian-style compact money: 1,24,000 → '1.24L', 24,000 → '24K',
    3,50,00,000 → '3.5Cr'. Up to 2 decimals, trailing zeros trimmed."""
    v = float(value or 0)

    def trim(x):
        return f"{x:.2f}".rstrip("0").rstrip(".")

    if v >= 1_00_00_000:
        return trim(v / 1_00_00_000) + "Cr"
    if v >= 1_00_000:
        return trim(v / 1_00_000) + "L"
    if v >= 1_000:
        return trim(v / 1_000) + "K"
    return trim(v)


@login_required
def business_overview(request):
    """Web Business Overview: period-grouped, product-split business trend +
    product mix + employee leaderboard (admins/managers). Mirrors the native
    'Business Overview' screen."""
    emp = getattr(request.user, "employee", None)
    is_admin = permissions.is_admin(request.user)
    if not permissions.is_admin_or_manager(request.user):
        return HttpResponseForbidden("Access denied")

    firm_wide = permissions.can(request.user, "employee_performance")

    base = Sale.objects.filter(status="approved")
    if not firm_wide and emp:
        base = base.filter(employee=emp)

    period = request.GET.get("period", "month")
    columns = request.GET.get("columns", 6)
    data = business_overview_data(base, period=period, columns=columns, with_leaderboard=firm_wide)

    buckets = [{"name": n, "color": _OVERVIEW_PALETTE[i % len(_OVERVIEW_PALETTE)]}
               for i, n in enumerate(data["buckets"])]

    # Stacked-bar geometry as absolute integer pixel heights, computed
    # server-side. Avoids depending on percentage-height resolution inside
    # flex items (collapses to 0 in some browsers) and on locale decimal
    # separators leaking into inline styles.
    PLOT_PX = 220
    max_amount = max((t["amount"] for t in data["trend"]), default=Decimal("0"))
    scale = (Decimal(PLOT_PX) / max_amount) if max_amount else Decimal("0")
    few_cols = len(data["trend"]) <= 8
    for t in data["trend"]:
        t["bar_px"] = int(round(float(t["amount"] * scale)))
        t["amount_label"] = _compact_inr(t["amount"])
        segs = []
        for i, v in enumerate(t["by_product"]):
            if v > 0:
                px = max(int(round(float(v * scale))), 2)  # keep tiny slices visible
                segs.append({
                    "name": buckets[i]["name"], "color": buckets[i]["color"], "amount": v,
                    "px": px, "label": _compact_inr(v),
                    "show_label": few_cols and px >= 20,  # only if it fits
                })
        t["segments"] = segs

    # Per-employee product cells + mini stacked bar (integer % widths).
    for e in data.get("leaderboard", []):
        e["cells"] = [{"color": buckets[i]["color"], "amount": v}
                      for i, v in enumerate(e["by_product"])]
        total = e["amount"] or Decimal("1")
        e["segments"] = [
            {"color": buckets[i]["color"], "pct": int(round(float(v / total * 100)))}
            for i, v in enumerate(e["by_product"]) if v > 0
        ]

    context = {
        "data": data,
        "buckets": buckets,
        "period": data["period"],
        "columns": data["columns"],
        "firm_wide": firm_wide,
        "periods": [("month", "Monthly"), ("quarter", "Quarterly"),
                    ("half", "Half-year"), ("year", "Yearly")],
        "column_choices": [3, 6, 9, 12, 18, 24],
    }
    return render(request, "reports/business_overview.html", context)


@login_required
def employee_past_performance(request):
    """Line chart of monthly points (last 12 months) for the logged-in employee."""
    emp = request.user.employee
    today = now().date()
    months = _last_n_months(today, n=12)

    labels = []
    points_data = []
    months_data = []

    for y, m in months:
        label = f"{month_name[m]} {y}"
        pts = (
            Sale.objects.filter(employee=emp, date__year=y, date__month=m)
            .aggregate(total=Sum("points"))["total"]
            or 0
        )
        labels.append(label)
        points_data.append(int(pts))
        months_data.append({"year": y, "month": m, "label": label, "points": int(pts)})

    total_points_12 = sum(points_data)
    avg_points = round(total_points_12 / len(points_data), 1) if points_data else 0
    best_month = max(months_data, key=lambda m: m["points"], default=None)
    recent_month = months_data[-1] if months_data else None
    prev_month_points = months_data[-2]["points"] if len(months_data) >= 2 else None
    trend_change = None
    trend_direction = "flat"

    if recent_month and prev_month_points is not None:
        trend_change = recent_month["points"] - prev_month_points
        trend_direction = "up" if trend_change > 0 else ("down" if trend_change < 0 else "flat")

    max_points = max(points_data) if points_data else 0

    context = {
        "labels_json": json.dumps(labels),
        "points_json": json.dumps(points_data),
        "months_data": months_data,
        "total_points_12": total_points_12,
        "avg_points": avg_points,
        "best_month": best_month,
        "recent_month": recent_month,
        "trend_change": trend_change,
        "trend_direction": trend_direction,
        "max_points": max_points,
    }
    return render(request, "dashboards/employee_past_performance.html", context)


@login_required
def past_month_performance(request, year, month):
    """Product-wise breakdown for an employee in a specific month."""
    emp = request.user.employee
    cat_map = category_name_map()

    product_sales = product_totals(
        Sale.objects.filter(employee=emp, date__year=year, date__month=month), cat_map
    )

    # Targets are set per product, so a category's target is the sum of its own
    # plus its sub-products'.
    target_map = {}
    for t in MonthlyTargetHistory.objects.filter(employee=emp, year=year, month=month):
        cat = cat_map.get(t.product, t.product)
        row = target_map.setdefault(cat, {"target_value": Decimal("0"), "achieved_value": Decimal("0")})
        row["target_value"] += t.target_value or Decimal("0")
        row["achieved_value"] += t.achieved_value or Decimal("0")

    products = []
    total_points = 0
    total_amount = 0
    max_points_product = 0
    for row in product_sales:
        prod = row["product"]
        prod_row = {
            "product": prod,
            "total_amount": row["total_amount"] or 0,
            "total_points": int(row["total_points"] or 0),
            "target_value": target_map.get(prod, {}).get("target_value"),
            "achieved_value": target_map.get(prod, {}).get("achieved_value"),
        }
        total_amount += prod_row["total_amount"]
        total_points += prod_row["total_points"]
        if prod_row["total_points"] > max_points_product:
            max_points_product = prod_row["total_points"]
        products.append(prod_row)

    for prod_row in products:
        prod_row["percent_of_max"] = (
            round((prod_row["total_points"] / max_points_product) * 100, 1) if max_points_product else 0
        )
        if prod_row["target_value"]:
            achieved_val = prod_row.get("achieved_value") or 0
            prod_row["achieved_percent"] = (
                round((achieved_val / prod_row["target_value"]) * 100, 1) if prod_row["target_value"] else None
            )
        else:
            prod_row["achieved_percent"] = None

    # Later years of multiyear policies land in this month with no sale behind
    # them. They are earned, so a month's report that leaves them out understates
    # what the employee made — but they are not business sold this month, so they
    # are their own line and never inside the product table.
    accrued_rows = incentives.accrued_rows(
        emp, date(year, month, 1), date(year, month, monthrange(year, month)[1]))
    accrued_points = sum((a.points for a in accrued_rows), Decimal("0"))

    context = {
        "year": year,
        "month": month,
        "month_label": f"{month_name[month]} {year}",
        "products": products,
        "total_points": total_points,
        "total_amount": total_amount,
        "accrued_rows": accrued_rows,
        "accrued_points": accrued_points,
        "earned_points": Decimal(str(total_points)) + accrued_points,
        "products_count": len(products),
        "max_points_product": max_points_product,
    }
    return render(request, "dashboards/past_month_performance.html", context)


@login_required
def admin_past_performance(request, n_months=12):
    emp = getattr(request.user, "employee", None)
    is_admin = permissions.is_admin(request.user)
    is_manager = bool(emp and emp.role == "manager")
    mgr_access = get_manager_access() if is_manager else None
    if not (is_admin or is_manager):
        return HttpResponseForbidden("Admins or managers only.")
    if is_manager and not permissions.can(request.user, "employee_performance"):
        return HttpResponseForbidden("Manager not allowed to view performance.")

    today = now().date()
    months = _last_n_months(today, n=n_months)

    year_options = sorted({y for (y, _) in months}, reverse=True)
    try:
        selected_year = int(request.GET.get("year"))
    except (TypeError, ValueError):
        selected_year = None
    if selected_year not in year_options:
        selected_year = year_options[0] if year_options else today.year

    months_for_year = [(y, m) for (y, m) in months if y == selected_year] or months

    employees_qs = Employee.objects.select_related("user").order_by("user__username")
    selected_employee_id = request.GET.get("employee")
    selected_employee = None
    if selected_employee_id and selected_employee_id != "all":
        try:
            selected_employee = employees_qs.get(pk=int(selected_employee_id))
        except (Employee.DoesNotExist, ValueError, TypeError):
            selected_employee = None

    # Chart: always show points history across the full last n_months window
    # so users see a true "past performance" trend instead of being limited
    # to whatever months exist in the selected year.
    labels = []
    totals_data = []
    for y, m in months:
        label = f"{month_name[m]} {y}"
        sale_filter = {"date__year": y, "date__month": m}
        if selected_employee:
            sale_filter["employee"] = selected_employee
        total_points = Sale.objects.filter(**sale_filter).aggregate(total=Sum("points"))["total"] or 0
        labels.append(label)
        totals_data.append(int(total_points))

    # Snapshot cards: keep the year filter so users can drill into a specific year.
    months_data = []
    for y, m in months_for_year:
        label = f"{month_name[m]} {y}"
        sale_filter = {"date__year": y, "date__month": m}
        if selected_employee:
            sale_filter["employee"] = selected_employee

        total_points = Sale.objects.filter(**sale_filter).aggregate(total=Sum("points"))["total"] or 0
        total_amount = Sale.objects.filter(**sale_filter).aggregate(total=Sum("amount"))["total"] or 0

        months_data.append({
            "year": y,
            "month": m,
            "label": label,
            "points": int(total_points),
            "amount": float(total_amount),
        })

    max_points_snapshot = max((md["points"] for md in months_data), default=0)
    for md in months_data:
        md["percent_of_max"] = round((md["points"] / max_points_snapshot) * 100, 1) if max_points_snapshot else 0

    latest_year, latest_month = months_for_year[-1] if months_for_year else months[-1]
    top_performers_qs = Sale.objects.filter(date__year=latest_year, date__month=latest_month)
    if selected_employee:
        top_performers_qs = top_performers_qs.filter(employee=selected_employee)

    top_performers_qs = (
        top_performers_qs.values(
            "employee__id",
            "employee__user__username",
            "employee__user__first_name",
            "employee__user__last_name",
        )
        .annotate(total_points=Sum("points"), total_amount=Sum("amount"))
        .order_by("-total_points")
    )

    top_performers = []
    for r in top_performers_qs:
        first = (r.get("employee__user__first_name") or "").strip()
        last = (r.get("employee__user__last_name") or "").strip()
        full_name = (first + " " + last).strip() if (first or last) else (r.get("employee__user__username") or "Unknown")
        top_performers.append({
            "employee_id": r.get("employee__id"),
            "username": r.get("employee__user__username") or "",
            "full_name": full_name,
            "total_points": int(r.get("total_points") or 0),
            "total_amount": float(r.get("total_amount") or 0),
        })

    context = {
        "labels_json": labels,
        "totals_json": totals_data,
        "months_data": months_data,
        "top_performers": top_performers,
        "latest_month_label": months_data[-1]["label"],
        "latest_year": latest_year,
        "latest_month": latest_month,
        "year_options": year_options,
        "selected_year": selected_year,
        "employees": employees_qs,
        "selected_employee_id": int(selected_employee.id) if selected_employee else None,
        "chart_label": "Total Points (all employees)" if not selected_employee else f"Points ({selected_employee.user.username})",
        "scope_label": "All Employees" if not selected_employee else f"{selected_employee.user.username}",
    }
    return render(request, "dashboards/admin_past_performance.html", context)


@login_required
def admin_past_month_performance(request, year, month):
    emp = getattr(request.user, "employee", None)
    is_admin_user = permissions.is_admin(request.user)
    is_manager = bool(emp and emp.role == "manager")
    mgr_access = get_manager_access() if is_manager else None
    if not permissions.can(request.user, "employee_performance"):
        return HttpResponseForbidden("Access denied.")

    cat_map = category_name_map()
    product_sales = product_totals(
        Sale.objects.filter(date__year=year, date__month=month), cat_map
    )

    # Targets are set per product, so a category's target is the sum of its own
    # plus its sub-products'.
    target_map = {}
    for t in MonthlyTargetHistory.objects.filter(year=year, month=month).values("product").annotate(
        target_value_sum=Sum("target_value"), achieved_value_sum=Sum("achieved_value")
    ):
        cat = cat_map.get(t["product"], t["product"])
        row = target_map.setdefault(cat, {"target_value": 0.0, "achieved_value": 0.0})
        row["target_value"] += float(t["target_value_sum"] or 0)
        row["achieved_value"] += float(t["achieved_value_sum"] or 0)

    products = []
    for row in product_sales:
        prod = row["product"]
        target_val = target_map.get(prod, {}).get("target_value")
        achieved_val = target_map.get(prod, {}).get("achieved_value")
        progress = 0
        if target_val:
            try:
                progress = (float(achieved_val or 0) / float(target_val)) * 100
            except Exception:
                progress = 0
        products.append({
            "product": prod,
            "total_amount": float(row["total_amount"] or 0),
            "total_points": int(row["total_points"] or 0),
            "target_value": target_val,
            "achieved_value": achieved_val,
            "progress": progress,
        })

    top_performers_qs = (
        Sale.objects.filter(date__year=year, date__month=month)
        .values(
            "employee__id",
            "employee__user__username",
            "employee__user__first_name",
            "employee__user__last_name",
        )
        .annotate(total_points=Sum("points"), total_amount=Sum("amount"))
        .order_by("-total_points")
    )

    top_performers = []
    for r in top_performers_qs:
        first = (r.get("employee__user__first_name") or "").strip()
        last = (r.get("employee__user__last_name") or "").strip()
        full_name = (first + " " + last).strip() if (first or last) else (r.get("employee__user__username") or "Unknown")
        top_performers.append({
            "employee_id": r.get("employee__id"),
            "username": r.get("employee__user__username") or "",
            "full_name": full_name,
            "total_points": int(r.get("total_points") or 0),
            "total_amount": float(r.get("total_amount") or 0),
        })

    # Per-product employee breakdown: for each product in this month, list which
    # employees sold it with their amount/points contribution.
    per_product_employee_qs = (
        Sale.objects.filter(date__year=year, date__month=month)
        .values(
            "product",
            "employee__id",
            "employee__user__username",
            "employee__user__first_name",
            "employee__user__last_name",
        )
        .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        .order_by("product", "-total_points")
    )

    # An employee selling two sub-products of the same category must appear once
    # in that category, with their contributions added — not as two rows.
    product_employee_map = {}
    for r in per_product_employee_qs:
        prod = cat_map.get(r["product"], r["product"])
        first = (r.get("employee__user__first_name") or "").strip()
        last = (r.get("employee__user__last_name") or "").strip()
        full_name = (first + " " + last).strip() if (first or last) else (r.get("employee__user__username") or "Unknown")
        by_emp = product_employee_map.setdefault(prod, {})
        row = by_emp.setdefault(r.get("employee__id"), {
            "employee_id": r.get("employee__id"),
            "username": r.get("employee__user__username") or "",
            "full_name": full_name,
            "total_points": 0,
            "total_amount": 0.0,
        })
        row["total_points"] += int(r.get("total_points") or 0)
        row["total_amount"] += float(r.get("total_amount") or 0)
    product_employee_map = {
        prod: sorted(by_emp.values(), key=lambda e: e["total_points"], reverse=True)
        for prod, by_emp in product_employee_map.items()
    }

    product_employee_stats = []
    for p in products:
        employees_for_prod = product_employee_map.get(p["product"], [])
        prod_total_points = p["total_points"] or 0
        for emp_row in employees_for_prod:
            emp_row["points_share"] = (
                round((emp_row["total_points"] / prod_total_points) * 100, 1)
                if prod_total_points else 0
            )
        product_employee_stats.append({
            "product": p["product"],
            "total_amount": p["total_amount"],
            "total_points": p["total_points"],
            "employees": employees_for_prod,
        })

    # Earlier-year multiyear credits, per employee. Kept out of `top_performers`
    # — that table ranks who sold what, and a policy sold two years ago is not
    # this month's selling — but shown beside it, because it IS money earned and
    # the payout has to reconcile.
    accrued_rows = incentives.accrued_rows(
        None, date(int(year), int(month), 1),
        date(int(year), int(month), monthrange(int(year), int(month))[1]))
    accrued_by_emp = {}
    for a in accrued_rows:
        row = accrued_by_emp.setdefault(a.employee_id, {
            "employee": a.employee, "points": Decimal("0"), "count": 0})
        row["points"] += a.points
        row["count"] += 1

    context = {
        "year": int(year),
        "month": int(month),
        "month_label": f"{month_name[int(month)]} {year}",
        "products": products,
        "top_performers": top_performers,
        "product_employee_stats": product_employee_stats,
        "accrued_rows": accrued_rows,
        "accrued_points": sum((a.points for a in accrued_rows), Decimal("0")),
        "accrued_by_emp": sorted(accrued_by_emp.values(),
                                 key=lambda r: r["points"], reverse=True),
    }
    return render(request, "dashboards/admin_past_month_performance.html", context)


def business_report_sheet(sel_date=None, sel_year=None, sel_month=None):
    """The Business Report grid, for one day (`sel_date`) or one month.

    One row per active employee, one column per reportable main product, plus
    accounts opened and points; grand totals alongside. Shared by the web page
    and the app's Daily Report — the roll-up rules below are fiddly enough that
    a second copy of them would drift.
    """
    daily = sel_date is not None
    if daily:
        approved = Sale.objects.filter(status="approved", date=sel_date)
    else:
        approved = Sale.objects.filter(status="approved", date__year=sel_year, date__month=sel_month)

    # Top-level products only. The life plan catalogue alone is ~40 sub-products,
    # and a column per plan makes this sheet unreadable — a sub-product's sales
    # roll up into its category (Term Plan -> Life Insurance).
    cat_map = category_name_map()
    products = list(
        Product.objects.filter(is_active=True, parent__isnull=True, show_in_reports=True)
        .order_by("display_order", "name")
        .values_list("name", flat=True)
    )
    # Products switched off for this sheet (Product Management). Their sales are
    # untouched — the column just isn't printed, including for a retired
    # category that would otherwise be re-added below because it has business.
    hidden = set(
        Product.objects.filter(parent__isnull=True, show_in_reports=False)
        .values_list("name", flat=True)
    )

    used_products = set(
        approved.exclude(product="")
        .values_list("product", flat=True)
        .distinct()
    )
    used_ref_products = set(
        approved.filter(product_ref__isnull=False)
        .values_list("product_ref__name", flat=True)
        .distinct()
    )

    # A retired category with sales this month still needs its column; a
    # sub-product never does, because cat_map folds it into its parent.
    for product_name in sorted(used_products | used_ref_products):
        category = cat_map.get(product_name, product_name)
        if category and category not in products and category not in hidden:
            products.append(category)
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__first_name")

    # Pre-aggregate amounts grouped by (employee, product) and points by employee.
    # Replaces an N×M loop of per-cell `.aggregate(Sum)` calls with two queries.
    amount_by_emp_product = {}
    for r in approved.values("employee_id", "product").annotate(total=Sum("amount")):
        key = (r["employee_id"], cat_map.get(r["product"], r["product"]))
        amount_by_emp_product[key] = (amount_by_emp_product.get(key, Decimal("0"))
                                      + (r["total"] or Decimal("0")))
    points_by_emp = {
        r["employee_id"]: r["total"] or Decimal("0")
        for r in approved.values("employee_id").annotate(total=Sum("points"))
    }
    # Accounts opened in the window. Client has no created_by, so the credit
    # goes to whoever the client is mapped to — which is also the figure the
    # firm reads this row for ("whose book grew").
    # ponytail: re-mapping a client moves its credit to the new employee; add
    # Client.created_by if that ever matters.
    new_clients = Client.objects.filter(mapped_to__isnull=False)
    if daily:
        new_clients = new_clients.filter(created_at__date=sel_date)
    else:
        new_clients = new_clients.filter(created_at__year=sel_year, created_at__month=sel_month)
    accounts_by_emp = {
        r["mapped_to"]: r["total"]
        for r in new_clients.values("mapped_to").order_by().annotate(total=Count("id"))
    }

    rows = []
    grand = {p: Decimal("0") for p in products}
    grand["points"] = Decimal("0")
    grand_accounts = 0

    for e in employees:
        product_vals = []
        for p in products:
            total = amount_by_emp_product.get((e.id, p), Decimal("0"))
            product_vals.append(total)
            grand[p] += total
        pts = points_by_emp.get(e.id, Decimal("0"))
        grand["points"] += pts
        accounts = accounts_by_emp.get(e.id, 0)
        grand_accounts += accounts
        rows.append({"employee": e, "product_vals": product_vals,
                     "points": pts, "accounts": accounts})

    return {
        "products": products,
        "rows": rows,
        "grand_vals": [grand[p] for p in products],
        "grand_points": grand["points"],
        "grand_accounts": grand_accounts,
    }


@login_required
def monthly_business_report(request, mode="month"):
    """The same sheet for a month or for a single day (`mode="day"`)."""
    if not permissions.is_admin_or_manager(request.user):
        return HttpResponseForbidden("Access denied")

    today = date.today()
    daily = mode == "day"
    sel_date = today
    if daily:
        try:
            sel_date = date.fromisoformat(request.GET.get("date", ""))
        except ValueError:
            sel_date = today
        sel_month, sel_year = sel_date.month, sel_date.year
        sheet = business_report_sheet(sel_date=sel_date)
    else:
        try:
            sel_month = int(request.GET.get("month", today.month))
            sel_year = int(request.GET.get("year", today.year))
        except (TypeError, ValueError):
            sel_month, sel_year = today.month, today.year
        if not 1 <= sel_month <= 12:
            sel_month = today.month
        sheet = business_report_sheet(sel_year=sel_year, sel_month=sel_month)

    context = {
        **sheet,
        "months": [(i, month_name[i]) for i in range(1, 13)],
        "years": list(range(today.year - 3, today.year + 1)),
        "sel_month": sel_month,
        "sel_year": sel_year,
        "month_name": month_name[sel_month],
        "daily": daily,
        "sel_date": sel_date,
        "period_label": sel_date.strftime("%d %b %Y") if daily else f"{month_name[sel_month]} {sel_year}",
    }
    return render(request, "reports/monthly_business_report.html", context)


# ---------------- Business Analytics (margin) ----------------

def _fy_months(fy_start_year):
    """Indian financial year months: Apr(start) → Mar(start+1)."""
    months = [(fy_start_year, m) for m in range(4, 13)]
    months += [(fy_start_year + 1, m) for m in range(1, 4)]
    return months


def _month_margin_breakdown(year, month):
    """Per-product (and Fresh/Port for health) margin for one month.

    Slabs match against the product's cumulative monthly revenue, so the
    revenue is aggregated per product/bucket first, then the margin % resolved.
    Returns (rows, totals) where each row has product, policy label, revenue,
    margin_percent and margin_amount.
    """
    approved = Sale.objects.filter(status="approved", date__year=year, date__month=month)
    products = list(Product.objects.all().select_related("parent")
                    .prefetch_related("margin_slabs").in_display_order())
    # PPT-priced plans carry a per-sale FYC snapshot, so their margin is summed
    # from the sales, not resolved from a revenue band.
    ppt_ids = set(Product.objects.filter(ppt_rates__isnull=False).values_list("id", flat=True))

    # One row per MAIN product. A plan's sale is its category's business — the
    # margin is still valued at the plan's own rate (per-sale FYC snapshot), so
    # rolling up blends the rates rather than losing them. Which plan earned
    # what is read on Product Management, where the rates are set.
    children = {}
    for p in products:
        if p.parent_id:
            children.setdefault(p.parent_id, []).append(p)

    rows = []
    total_rev = Decimal("0")
    total_margin = Decimal("0")

    for p in (x for x in products if not x.parent_id):
        kids = children.get(p.pk, [])
        plabel = p.name
        base = approved.filter(
            Q(product_ref=p)
            | Q(product_ref__parent_id=p.pk)
            | (Q(product_ref__isnull=True) & Q(product__in=[p.name] + [k.name for k in kids]))
        )

        if p.id in ppt_ids or any(k.id in ppt_ids for k in kids):
            # Each sale's frozen FYC snapshot values its own amount.
            rev = Decimal("0")
            amt = Decimal("0")
            unrated = Decimal("0")
            for s_amount, s_pct in base.values_list("amount", "margin_percent_snapshot"):
                s_amount = s_amount or Decimal("0")
                rev += s_amount
                if s_amount and s_pct:
                    amt += (s_amount * s_pct / Decimal("100"))
                else:
                    # A plan sold without a rate chart falls back to the
                    # category's band rather than counting as zero margin.
                    unrated += s_amount
            if rev <= 0:
                continue
            if unrated > 0:
                amt += unrated * p.margin_for(unrated, "") / Decimal("100")
            amt = amt.quantize(Decimal("0.01"))
            pct = (amt / rev * Decimal("100")).quantize(Decimal("0.01")) if rev else Decimal("0.00")
            rows.append({"product": plabel, "policy": "", "revenue": rev,
                         "margin_percent": pct, "margin_amount": amt})
            total_rev += rev
            total_margin += amt
        elif p.is_health:
            buckets = [("fresh", "Fresh"), ("port", "Port")]
            seen_codes = []
            for code, label in buckets:
                seen_codes.append(code)
                # Multiyear health: only the first-year slice is this month's
                # business (the rest renews in later years).
                rev = base.filter(policy_type=code).aggregate(t=Sum(_ANNUAL_SLICE))["t"] or Decimal("0")
                rev = rev.quantize(Decimal("0.01"))
                if rev <= 0:
                    continue
                # Health slabs are a band on the CATEGORY's monthly volume,
                # so the category is the right level to resolve them at.
                pct = p.margin_for(rev, code)
                amt = (rev * pct / Decimal("100")).quantize(Decimal("0.01"))
                rows.append({"product": plabel, "policy": label, "revenue": rev,
                             "margin_percent": pct, "margin_amount": amt})
                total_rev += rev
                total_margin += amt
            rev_unset = base.exclude(policy_type__in=seen_codes).aggregate(t=Sum(_ANNUAL_SLICE))["t"] or Decimal("0")
            rev_unset = rev_unset.quantize(Decimal("0.01"))
            if rev_unset > 0:
                pct = p.margin_for(rev_unset, "")
                amt = (rev_unset * pct / Decimal("100")).quantize(Decimal("0.01"))
                rows.append({"product": plabel, "policy": "Unspecified", "revenue": rev_unset,
                             "margin_percent": pct, "margin_amount": amt})
                total_rev += rev_unset
                total_margin += amt
        else:
            # The row is the category's, but each plan's revenue is valued at
            # its OWN rate before blending — rolling up must not quietly reprice
            # a sub-product at its parent's margin.
            members = {m.pk: m for m in [p] + kids}
            by_name = {m.name: m for m in members.values()}
            rev = Decimal("0")
            amt = Decimal("0")
            for r in base.values("product_ref", "product").order_by().annotate(t=Sum("amount")):
                m_rev = r["t"] or Decimal("0")
                if m_rev <= 0:
                    continue
                member = members.get(r["product_ref"]) or by_name.get((r["product"] or "").strip(), p)
                rev += m_rev
                amt += m_rev * member.margin_for(m_rev, "") / Decimal("100")
            if rev <= 0:
                continue
            amt = amt.quantize(Decimal("0.01"))
            pct = (amt / rev * Decimal("100")).quantize(Decimal("0.01")) if rev else Decimal("0.00")
            rows.append({"product": plabel, "policy": "", "revenue": rev,
                         "margin_percent": pct, "margin_amount": amt})
            total_rev += rev
            total_margin += amt

    # Approved sales not mapped to any known product → 0% margin, kept so totals reconcile.
    product_names = [p.name for p in products]
    leftover = approved.filter(product_ref__isnull=True).exclude(product__in=product_names)
    leftover_rev = leftover.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    if leftover_rev > 0:
        rows.append({"product": "Other / Unmapped", "policy": "", "revenue": leftover_rev,
                     "margin_percent": Decimal("0.00"), "margin_amount": Decimal("0.00")})
        total_rev += leftover_rev

    blended = (total_margin / total_rev * Decimal("100")).quantize(Decimal("0.01")) if total_rev else Decimal("0.00")
    totals = {"revenue": total_rev, "margin_amount": total_margin, "blended_percent": blended}
    return rows, totals


def _month_renewal_breakdown(year, month):
    """Per-product renewal margin for one month, by premium collected.

    Renewal premium is attributed to the month it was collected, grouped by
    the linked Product and valued at that product's flat renewal margin %.
    Renewals not linked to a Product are kept as a 0%-margin row so the
    revenue totals reconcile.
    """
    qs = Renewal.objects.filter(
        premium_collected_on__year=year, premium_collected_on__month=month
    )

    rows = []
    total_rev = Decimal("0")
    total_margin = Decimal("0")

    per_product = (
        qs.filter(product_ref__isnull=False)
        .values("product_ref")
        .annotate(total=Sum("premium_amount"))
    )
    products = {p.pk: p for p in Product.objects.filter(
        pk__in=[r["product_ref"] for r in per_product]
    ).select_related("parent")}

    # One row per main product; a sub-product's premium is still valued at its
    # own renewal rate, so the row's % is the blend of the plans behind it.
    by_category = {}
    for r in per_product:
        rev = r["total"] or Decimal("0")
        if rev <= 0:
            continue
        p = products.get(r["product_ref"])
        pct = p.renewal_margin_percent if p else Decimal("0.00")
        category = p.category if p else None
        key = category.pk if category else 0
        row = by_category.setdefault(key, {
            "product": category.name if category else "—",
            "revenue": Decimal("0"), "margin_amount": Decimal("0"),
        })
        row["revenue"] += rev
        row["margin_amount"] += rev * pct / Decimal("100")

    for row in by_category.values():
        row["margin_amount"] = row["margin_amount"].quantize(Decimal("0.01"))
        row["margin_percent"] = (
            (row["margin_amount"] / row["revenue"] * Decimal("100")).quantize(Decimal("0.01"))
            if row["revenue"] else Decimal("0.00")
        )
        rows.append(row)
        total_rev += row["revenue"]
        total_margin += row["margin_amount"]

    unmapped_rev = (
        qs.filter(product_ref__isnull=True).aggregate(t=Sum("premium_amount"))["t"]
        or Decimal("0")
    )
    if unmapped_rev > 0:
        rows.append({"product": "Other / Unmapped (Renewal)", "revenue": unmapped_rev,
                     "margin_percent": Decimal("0.00"), "margin_amount": Decimal("0.00")})
        total_rev += unmapped_rev

    # ── Multiyear health: years 2..N of a multiyear policy are recognized as
    # renewal business on each anniversary (the first year was Fresh at sale).
    # Derived from the sale, so nothing to create/maintain.
    health = Product.objects.filter(code="HEALTH_INS").first()
    if health:
        def _add_years(d, n):
            try:
                return d.replace(year=d.year + n)
            except ValueError:  # 29 Feb → 28 Feb
                return d.replace(year=d.year + n, day=28)

        my_slice = Decimal("0")
        # Sub-products roll up: a multiyear sale naming a specific health plan
        # is still health business. `_is_health_product` already counts it for
        # points, so matching the code alone here would pay the employee for a
        # year the margin report never saw.
        for sale in Sale.objects.filter(
            Q(product_ref=health) | Q(product_ref__parent=health),
            policy_years__gt=1, status=Sale.STATUS_APPROVED,
        ).only("amount", "policy_years", "policy_date", "date"):
            base_date = sale.policy_date or sale.date
            for k in range(1, sale.policy_years):  # anniversaries → years 2..N
                anniv = _add_years(base_date, k)
                if anniv.year == year and anniv.month == month:
                    my_slice += sale.annual_premium
        if my_slice > 0:
            pct = health.renewal_margin_percent or Decimal("0.00")
            amt = (my_slice * pct / Decimal("100")).quantize(Decimal("0.01"))
            label = f"{health.parent.name} › {health.name}" if health.parent_id else health.name
            existing = next((r for r in rows if r["product"] == label), None)
            if existing:
                existing["revenue"] += my_slice
                existing["margin_amount"] += amt
                existing["margin_percent"] = (
                    (existing["margin_amount"] / existing["revenue"] * Decimal("100")).quantize(Decimal("0.01"))
                    if existing["revenue"] else Decimal("0.00")
                )
            else:
                rows.append({"product": label, "revenue": my_slice,
                             "margin_percent": pct, "margin_amount": amt})
            total_rev += my_slice
            total_margin += amt

    blended = (total_margin / total_rev * Decimal("100")).quantize(Decimal("0.01")) if total_rev else Decimal("0.00")
    rows.sort(key=lambda x: x["margin_amount"], reverse=True)
    totals = {"revenue": total_rev, "margin_amount": total_margin, "blended_percent": blended}
    return rows, totals


def _monthly_salary_total():
    """Current total monthly salary across active employees."""
    return Employee.objects.filter(active=True).aggregate(t=Sum("salary"))["t"] or Decimal("0")


def _period_expense_breakdown(months):
    """months: list of (year, month). Returns (category_rows, total).

    One-time expenses count in the month they were incurred; recurring
    expenses count their per-month amount for each active month.
    """
    expenses = list(Expense.objects.select_related("category").all())
    cat_totals = {}
    total = Decimal("0")
    for (y, m) in months:
        for e in expenses:
            if e.applies_to_month(y, m):
                cat_totals[e.category.name] = cat_totals.get(e.category.name, Decimal("0")) + e.amount
                total += e.amount
    rows = [{"category": k, "amount": v} for k, v in sorted(cat_totals.items())]
    return rows, total


def _parse_expense_post(request):
    """Validate add-expense form fields. Returns (kwargs, error)."""
    try:
        category = ExpenseCategory.objects.get(pk=request.POST.get("category_id"))
    except (ExpenseCategory.DoesNotExist, ValueError, TypeError):
        return None, "Pick a valid expense category."

    expense_type = request.POST.get("expense_type")
    if expense_type not in (Expense.TYPE_ONE_TIME, Expense.TYPE_RECURRING):
        return None, "Choose One-time or Recurring."

    try:
        amount = Decimal(str(request.POST.get("amount", "")).strip())
    except (InvalidOperation, TypeError):
        return None, "Enter a valid amount."
    if amount < 0:
        return None, "Amount cannot be negative."

    def _date(field):
        raw = (request.POST.get(field) or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return False

    spent_on = _date("spent_on")
    if spent_on in (None, False):
        return None, "Enter a valid date."
    end_on = _date("end_on")
    if end_on is False:
        return None, "Enter a valid end date or leave it blank."
    if expense_type == Expense.TYPE_ONE_TIME:
        end_on = None
    elif end_on is not None and end_on < spent_on:
        return None, "Recurring end month cannot be before the start month."

    return {
        "category": category,
        "expense_type": expense_type,
        "amount": amount,
        "spent_on": spent_on,
        "end_on": end_on,
        "note": (request.POST.get("note") or "").strip()[:255],
    }, None


def _is_ba_admin(request):
    """Analytics pages (Business Analytics) are admin-only."""
    return permissions.is_admin(request.user)


@login_required
def business_analytics(request):
    if not _is_ba_admin(request):
        return HttpResponseForbidden("Access denied")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "add_expense":
            kwargs, err = _parse_expense_post(request)
            if err:
                messages.error(request, err)
            else:
                Expense.objects.create(created_by=request.user, **kwargs)
                messages.success(request, "Expense added.")

        elif action == "delete_expense":
            try:
                Expense.objects.filter(pk=request.POST.get("expense_id")).delete()
                messages.success(request, "Expense removed.")
            except (ValueError, TypeError):
                messages.error(request, "Invalid expense.")

        elif action == "add_expense_category":
            name = (request.POST.get("name") or "").strip()
            if not name:
                messages.error(request, "Category name is required.")
            elif ExpenseCategory.objects.filter(name__iexact=name).exists():
                messages.error(request, f"Category '{name}' already exists.")
            else:
                ExpenseCategory.objects.create(name=name)
                messages.success(request, f"Category '{name}' added.")

        elif action == "toggle_expense_category":
            cat = ExpenseCategory.objects.filter(pk=request.POST.get("category_id")).first()
            if cat:
                cat.is_active = not cat.is_active
                cat.save(update_fields=["is_active"])
                messages.success(
                    request,
                    f"Category '{cat.name}' {'restored' if cat.is_active else 'archived'}.",
                )
            else:
                messages.error(request, "Invalid category.")
        else:
            messages.error(request, "Unsupported action.")

        return redirect(request.get_full_path())

    today = date.today()
    view_mode = request.GET.get("view", "monthly")
    if view_mode not in ("monthly", "annual"):
        view_mode = "monthly"
    include_salaries = request.GET.get("salaries", "1") != "0"

    # Financial year that contains `today` (Apr–Mar).
    current_fy_start = today.year if today.month >= 4 else today.year - 1
    fy_years = list(range(current_fy_start - 4, current_fy_start + 1))
    months = [(i, month_name[i]) for i in range(1, 13)]
    years = list(range(today.year - 4, today.year + 1))

    context = {
        "view_mode": view_mode,
        "months": months,
        "years": years,
        "fy_years": fy_years,
    }

    if view_mode == "annual":
        try:
            fy_start = int(request.GET.get("fy", current_fy_start))
        except (TypeError, ValueError):
            fy_start = current_fy_start

        month_summary = []
        product_agg = {}  # key -> {product, policy, revenue, margin_amount}
        fy_rev = Decimal("0")
        fy_margin = Decimal("0")

        renewal_agg = {}
        fy_renew_rev = Decimal("0")
        fy_renew_margin = Decimal("0")

        for (y, m) in _fy_months(fy_start):
            rows, totals = _month_margin_breakdown(y, m)
            r_rows, r_totals = _month_renewal_breakdown(y, m)
            month_summary.append({
                "year": y, "month": m, "label": f"{month_name[m][:3]} {y}",
                "revenue": totals["revenue"], "margin_amount": totals["margin_amount"],
                "blended_percent": totals["blended_percent"],
                "renewal_revenue": r_totals["revenue"],
                "renewal_margin": r_totals["margin_amount"],
            })
            fy_rev += totals["revenue"]
            fy_margin += totals["margin_amount"]
            fy_renew_rev += r_totals["revenue"]
            fy_renew_margin += r_totals["margin_amount"]
            for r in rows:
                key = (r["product"], r["policy"])
                agg = product_agg.setdefault(key, {
                    "product": r["product"], "policy": r["policy"],
                    "revenue": Decimal("0"), "margin_amount": Decimal("0"),
                })
                agg["revenue"] += r["revenue"]
                agg["margin_amount"] += r["margin_amount"]
            for r in r_rows:
                agg = renewal_agg.setdefault(r["product"], {
                    "product": r["product"],
                    "revenue": Decimal("0"), "margin_amount": Decimal("0"),
                })
                agg["revenue"] += r["revenue"]
                agg["margin_amount"] += r["margin_amount"]

        product_rows = []
        for agg in product_agg.values():
            eff = (agg["margin_amount"] / agg["revenue"] * Decimal("100")).quantize(Decimal("0.01")) if agg["revenue"] else Decimal("0.00")
            product_rows.append({**agg, "effective_percent": eff})
        product_rows.sort(key=lambda x: x["margin_amount"], reverse=True)

        renewal_rows = []
        for agg in renewal_agg.values():
            eff = (agg["margin_amount"] / agg["revenue"] * Decimal("100")).quantize(Decimal("0.01")) if agg["revenue"] else Decimal("0.00")
            renewal_rows.append({**agg, "effective_percent": eff})
        renewal_rows.sort(key=lambda x: x["margin_amount"], reverse=True)

        fy_blended = (fy_margin / fy_rev * Decimal("100")).quantize(Decimal("0.01")) if fy_rev else Decimal("0.00")
        renew_blended = (fy_renew_margin / fy_renew_rev * Decimal("100")).quantize(Decimal("0.01")) if fy_renew_rev else Decimal("0.00")
        period_months = _fy_months(fy_start)
        period_revenue = fy_rev + fy_renew_rev
        gross_margin = fy_margin + fy_renew_margin
        context.update({
            "fy_start": fy_start,
            "fy_label": f"FY {fy_start}-{str(fy_start + 1)[-2:]}",
            "month_summary": month_summary,
            "product_rows": product_rows,
            "fy_totals": {"revenue": fy_rev, "margin_amount": fy_margin, "blended_percent": fy_blended},
            "renewal_rows": renewal_rows,
            "renewal_totals": {"revenue": fy_renew_rev, "margin_amount": fy_renew_margin, "blended_percent": renew_blended},
        })
    else:
        try:
            sel_month = int(request.GET.get("month", today.month))
            sel_year = int(request.GET.get("year", today.year))
        except (TypeError, ValueError):
            sel_month, sel_year = today.month, today.year

        rows, totals = _month_margin_breakdown(sel_year, sel_month)
        rows.sort(key=lambda x: x["margin_amount"], reverse=True)
        renewal_rows, renewal_totals = _month_renewal_breakdown(sel_year, sel_month)
        period_months = [(sel_year, sel_month)]
        period_revenue = totals["revenue"] + renewal_totals["revenue"]
        gross_margin = totals["margin_amount"] + renewal_totals["margin_amount"]
        context.update({
            "sel_month": sel_month,
            "sel_year": sel_year,
            "month_name": month_name[sel_month],
            "rows": rows,
            "totals": totals,
            "renewal_rows": renewal_rows,
            "renewal_totals": renewal_totals,
        })

    # ---- Expenses + Net Margin (shared by both views) ----
    expense_rows, expense_total = _period_expense_breakdown(period_months)
    monthly_salary = _monthly_salary_total()
    salary_total = monthly_salary * len(period_months)
    salary_applied = salary_total if include_salaries else Decimal("0")
    net_margin = gross_margin - expense_total - salary_applied
    net_margin_percent = (
        (net_margin / period_revenue * Decimal("100")).quantize(Decimal("0.01"))
        if period_revenue else Decimal("0.00")
    )

    # Query string used by management forms so a POST redirects back to the
    # same view/period the user is looking at.
    qs_params = {"view": view_mode}
    if view_mode == "annual":
        qs_params["fy"] = context["fy_start"]
    else:
        qs_params["month"] = context["sel_month"]
        qs_params["year"] = context["sel_year"]
    qs_nosal = urlencode(qs_params)  # period only, no salaries flag
    qs_params = {**qs_params, "salaries": "1" if include_salaries else "0"}

    context.update({
        "include_salaries": include_salaries,
        "expense_rows": expense_rows,
        "expense_total": expense_total,
        "monthly_salary": monthly_salary,
        "salary_total": salary_total,
        "salary_applied": salary_applied,
        "total_costs": expense_total + salary_applied,
        "gross_margin": gross_margin,
        "net_margin": net_margin,
        "net_margin_percent": net_margin_percent,
        "expense_categories": ExpenseCategory.objects.all().order_by("display_order", "name"),
        "active_expense_categories": ExpenseCategory.objects.filter(is_active=True).order_by("display_order", "name"),
        "all_expenses": Expense.objects.select_related("category").all()[:200],
        "expense_type_choices": Expense.TYPE_CHOICES,
        "ba_qs": urlencode(qs_params),
        "ba_qs_nosal": qs_nosal,
        "today_iso": today.isoformat(),
    })
    return render(request, "reports/business_analytics.html", context)

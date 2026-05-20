"""Reports views: past performance, monthly business report."""
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from calendar import month_name
from urllib.parse import urlencode

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.db.models import Sum, Q
from django.utils.timezone import now

from ..models import (
    Sale, Employee, MonthlyTargetHistory, Product, Expense, ExpenseCategory,
    Renewal, MFSnapshot, MFProjectionSettings,
)
from ..services.mf_engine import (
    build_dashboard, reconcile, historical_analytics,
)
from .helpers import get_manager_access, _last_n_months


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

    product_sales = (
        Sale.objects.filter(employee=emp, date__year=year, date__month=month)
        .values("product")
        .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        .order_by("-total_amount")
    )

    target_history = MonthlyTargetHistory.objects.filter(employee=emp, year=year, month=month)
    target_map = {t.product: t for t in target_history}

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
            "target_value": target_map.get(prod).target_value if prod in target_map else None,
            "achieved_value": target_map.get(prod).achieved_value if prod in target_map else None,
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

    context = {
        "year": year,
        "month": month,
        "month_label": f"{month_name[month]} {year}",
        "products": products,
        "total_points": total_points,
        "total_amount": total_amount,
        "products_count": len(products),
        "max_points_product": max_points_product,
    }
    return render(request, "dashboards/past_month_performance.html", context)


@login_required
def admin_past_performance(request, n_months=12):
    emp = getattr(request.user, "employee", None)
    is_admin = bool(emp and emp.role == "admin")
    is_manager = bool(emp and emp.role == "manager")
    mgr_access = get_manager_access() if is_manager else None
    if not (is_admin or is_manager):
        return HttpResponseForbidden("Admins or managers only.")
    if is_manager and not (mgr_access and mgr_access.allow_employee_performance):
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
    is_admin_user = request.user.is_superuser or (emp and emp.role == "admin")
    is_manager = bool(emp and emp.role == "manager")
    mgr_access = get_manager_access() if is_manager else None
    if not (is_admin_user or (is_manager and mgr_access and mgr_access.allow_employee_performance)):
        return HttpResponseForbidden("Access denied.")

    product_sales = (
        Sale.objects.filter(date__year=year, date__month=month)
        .values("product")
        .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        .order_by("-total_amount")
    )

    target_history = MonthlyTargetHistory.objects.filter(year=year, month=month)
    target_map = {}
    if target_history.exists():
        summed_targets = target_history.values("product").annotate(
            target_value_sum=Sum("target_value"), achieved_value_sum=Sum("achieved_value")
        )
        for t in summed_targets:
            target_map[t["product"]] = {
                "target_value": float(t["target_value_sum"] or 0),
                "achieved_value": float(t["achieved_value_sum"] or 0),
            }

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

    product_employee_map = {}
    for r in per_product_employee_qs:
        prod = r["product"]
        first = (r.get("employee__user__first_name") or "").strip()
        last = (r.get("employee__user__last_name") or "").strip()
        full_name = (first + " " + last).strip() if (first or last) else (r.get("employee__user__username") or "Unknown")
        product_employee_map.setdefault(prod, []).append({
            "employee_id": r.get("employee__id"),
            "username": r.get("employee__user__username") or "",
            "full_name": full_name,
            "total_points": int(r.get("total_points") or 0),
            "total_amount": float(r.get("total_amount") or 0),
        })

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

    context = {
        "year": int(year),
        "month": int(month),
        "month_label": f"{month_name[int(month)]} {year}",
        "products": products,
        "top_performers": top_performers,
        "product_employee_stats": product_employee_stats,
    }
    return render(request, "dashboards/admin_past_month_performance.html", context)


@login_required
def monthly_business_report(request):
    emp = request.user.employee
    if emp.role not in ("admin", "manager"):
        return HttpResponseForbidden("Access denied")

    today = date.today()
    sel_month = int(request.GET.get("month", today.month))
    sel_year = int(request.GET.get("year", today.year))

    approved = Sale.objects.filter(status="approved", date__year=sel_year, date__month=sel_month)

    # Show all active products, and include disabled products only if they have sales in the selected month.
    products = list(
        Product.objects.filter(is_active=True)
        .order_by("display_order", "name")
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

    for product_name in sorted(used_products | used_ref_products):
        if product_name and product_name not in products:
            products.append(product_name)
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__first_name")

    # Pre-aggregate amounts grouped by (employee, product) and points by employee.
    # Replaces an N×M loop of per-cell `.aggregate(Sum)` calls with two queries.
    amount_by_emp_product = {
        (r["employee_id"], r["product"]): r["total"] or Decimal("0")
        for r in approved.values("employee_id", "product").annotate(total=Sum("amount"))
    }
    points_by_emp = {
        r["employee_id"]: r["total"] or Decimal("0")
        for r in approved.values("employee_id").annotate(total=Sum("points"))
    }

    rows = []
    grand = {p: Decimal("0") for p in products}
    grand["points"] = Decimal("0")

    for e in employees:
        product_vals = []
        for p in products:
            total = amount_by_emp_product.get((e.id, p), Decimal("0"))
            product_vals.append(total)
            grand[p] += total
        pts = points_by_emp.get(e.id, Decimal("0"))
        grand["points"] += pts
        rows.append({"employee": e, "product_vals": product_vals, "points": pts})

    grand_vals = [grand[p] for p in products]
    months = [(i, month_name[i]) for i in range(1, 13)]
    years = list(range(today.year - 3, today.year + 1))

    context = {
        "rows": rows,
        "products": products,
        "grand_vals": grand_vals,
        "grand_points": grand["points"],
        "months": months,
        "years": years,
        "sel_month": sel_month,
        "sel_year": sel_year,
        "month_name": month_name[sel_month],
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
    products = list(Product.objects.all().order_by("display_order", "name"))

    rows = []
    total_rev = Decimal("0")
    total_margin = Decimal("0")
    matched_pks = []

    for p in products:
        base = approved.filter(
            Q(product_ref=p) | (Q(product_ref__isnull=True) & Q(product=p.name))
        )
        matched_pks.append(p.pk)

        if p.is_health:
            buckets = [("fresh", "Fresh"), ("port", "Port")]
            seen_codes = []
            for code, label in buckets:
                seen_codes.append(code)
                rev = base.filter(policy_type=code).aggregate(t=Sum("amount"))["t"] or Decimal("0")
                if rev <= 0:
                    continue
                pct = p.margin_for(rev, code)
                amt = (rev * pct / Decimal("100")).quantize(Decimal("0.01"))
                rows.append({"product": p.name, "policy": label, "revenue": rev,
                             "margin_percent": pct, "margin_amount": amt})
                total_rev += rev
                total_margin += amt
            rev_unset = base.exclude(policy_type__in=seen_codes).aggregate(t=Sum("amount"))["t"] or Decimal("0")
            if rev_unset > 0:
                pct = p.margin_for(rev_unset, "")
                amt = (rev_unset * pct / Decimal("100")).quantize(Decimal("0.01"))
                rows.append({"product": p.name, "policy": "Unspecified", "revenue": rev_unset,
                             "margin_percent": pct, "margin_amount": amt})
                total_rev += rev_unset
                total_margin += amt
        else:
            rev = base.aggregate(t=Sum("amount"))["t"] or Decimal("0")
            if rev <= 0:
                continue
            pct = p.margin_for(rev, "")
            amt = (rev * pct / Decimal("100")).quantize(Decimal("0.01"))
            rows.append({"product": p.name, "policy": "", "revenue": rev,
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
    )}
    for r in per_product:
        rev = r["total"] or Decimal("0")
        if rev <= 0:
            continue
        p = products.get(r["product_ref"])
        pct = p.renewal_margin_percent if p else Decimal("0.00")
        amt = (rev * pct / Decimal("100")).quantize(Decimal("0.01"))
        rows.append({"product": p.name if p else "—", "revenue": rev,
                     "margin_percent": pct, "margin_amount": amt})
        total_rev += rev
        total_margin += amt

    unmapped_rev = (
        qs.filter(product_ref__isnull=True).aggregate(t=Sum("premium_amount"))["t"]
        or Decimal("0")
    )
    if unmapped_rev > 0:
        rows.append({"product": "Other / Unmapped (Renewal)", "revenue": unmapped_rev,
                     "margin_percent": Decimal("0.00"), "margin_amount": Decimal("0.00")})
        total_rev += unmapped_rev

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
    """Analytics pages (Business Analytics, MF Revenue Engine) are admin-only."""
    if request.user.is_superuser:
        return True
    emp = getattr(request.user, "employee", None)
    return bool(emp and emp.role == "admin")


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


# ---------------- MF Revenue Engine (MFD module, Phase 1) ----------------

_MF_DECIMAL_FIELDS = [
    "gross_sip_registered", "active_sip_book", "stopped_sip_amount",
    "new_lumpsum", "redemptions", "trail_income",
    "insurance_new_business", "insurance_renewals",
]
_MF_NULLABLE_AUM_FIELDS = ["opening_aum", "closing_aum"]
_MF_SETTINGS_FIELDS = [
    "annual_market_growth_pct", "redemption_rate_pct",
    "sip_stoppage_rate_pct", "projection_trail_pct",
]


def _dec(raw, default=Decimal("0")):
    if raw in (None, ""):
        return default
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, TypeError):
        return default


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return False  # sentinel: present but invalid


@login_required
def mf_revenue_engine(request):
    if not _is_ba_admin(request):
        return HttpResponseForbidden("Access denied")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "save_snapshot":
            start = _parse_date(request.POST.get("start_date"))
            end = _parse_date(request.POST.get("end_date"))
            if start in (None, False) or end in (None, False):
                messages.error(request, "Both a valid start date and end date are required.")
                return redirect("clients:mf_revenue_engine")
            if end < start:
                messages.error(request, "End date must be on or after start date.")
                return redirect("clients:mf_revenue_engine")

            values = {f: _dec(request.POST.get(f)) for f in _MF_DECIMAL_FIELDS}
            if any(v < 0 for v in values.values()):
                messages.error(request, "Values cannot be negative.")
                return redirect("clients:mf_revenue_engine")
            # AUM is optional — blank means 'not known for this period'.
            for f in _MF_NULLABLE_AUM_FIELDS:
                raw = (request.POST.get(f) or "").strip()
                if raw == "":
                    values[f] = None
                else:
                    v = _dec(raw, default=None)
                    if v is None or v < 0:
                        messages.error(request, f"{f.replace('_', ' ').title()} must be a non-negative number or blank.")
                        return redirect("clients:mf_revenue_engine")
                    values[f] = v
            values["notes"] = (request.POST.get("notes") or "").strip()[:255]

            snap_id = request.POST.get("snapshot_id")
            if snap_id:
                snap = MFSnapshot.objects.filter(pk=snap_id).first()
                if not snap:
                    messages.error(request, "Snapshot not found.")
                    return redirect("clients:mf_revenue_engine")
                # Prevent collision with another row on the same exact range.
                dup = MFSnapshot.objects.exclude(pk=snap.pk).filter(
                    start_date=start, end_date=end).exists()
                if dup:
                    messages.error(request, "Another snapshot already covers that exact range.")
                    return redirect("clients:mf_revenue_engine")
                snap.start_date = start; snap.end_date = end
                for f, v in values.items():
                    setattr(snap, f, v)
                snap.save()
                messages.success(request, "Snapshot updated.")
            else:
                if MFSnapshot.objects.filter(start_date=start, end_date=end).exists():
                    messages.error(request, "A snapshot for that exact range already exists — edit it instead.")
                    return redirect("clients:mf_revenue_engine")
                snap = MFSnapshot.objects.create(
                    start_date=start, end_date=end,
                    created_by=request.user, **values,
                )
                messages.success(request, "Snapshot added.")
            return redirect(f"{request.path}?snap={snap.pk}")

        if action == "delete_snapshot":
            MFSnapshot.objects.filter(pk=request.POST.get("snapshot_id")).delete()
            messages.success(request, "Snapshot deleted.")
            return redirect("clients:mf_revenue_engine")

        if action == "save_settings":
            ps = MFProjectionSettings.current()
            for f in _MF_SETTINGS_FIELDS:
                setattr(ps, f, _dec(request.POST.get(f), default=getattr(ps, f)))
            ps.updated_by = request.user
            ps.save()
            messages.success(request, "Projection settings saved.")
            return redirect("clients:mf_revenue_engine")

        messages.error(request, "Unsupported action.")
        return redirect("clients:mf_revenue_engine")

    snapshots = list(MFSnapshot.objects.all())  # newest first
    settings_obj = MFProjectionSettings.current()
    today = date.today()

    selected = None
    snap_id = request.GET.get("snap")
    if snap_id:
        selected = next((s for s in snapshots if str(s.pk) == str(snap_id)), None)
    if selected is None and snapshots:
        selected = snapshots[0]

    # ?edit=ID puts the form in edit mode. Viewing a snapshot via ?snap=ID
    # does NOT pre-fill the form — the form is always 'Add new' unless the
    # user explicitly clicked Edit. This is what makes saving the form
    # always create a new row by default.
    editing = None
    edit_id = request.GET.get("edit")
    if edit_id:
        editing = next((s for s in snapshots if str(s.pk) == str(edit_id)), None)

    # History oldest → newest; reconcile each against its prior (by end_date).
    history = sorted(snapshots, key=lambda s: (s.start_date, s.end_date))
    prev_by_pk = {}
    for i, s in enumerate(history):
        prev_by_pk[s.pk] = history[i - 1] if i > 0 else None

    history_rows = []
    recon_chart = {"labels": [], "operational": [], "market": []}
    for s in history:
        rec = reconcile(s, prev_by_pk[s.pk])
        label = f"{s.start_date:%d-%b-%y} → {s.end_date:%d-%b-%y}"
        history_rows.append({
            "label": label,
            "opening_aum": s.opening_aum,
            "closing_aum": s.closing_aum,
            "active_sip_book": s.active_sip_book,
            "trail_income": s.trail_income,
            "operational_growth": rec["operational_growth"],
            "market_movement_impact": rec["market_movement_impact"],
            "net_aum_growth": rec["net_aum_growth"],
            "projection_accuracy": rec["projection_accuracy"],
            "pk": s.pk,
            "is_selected": selected is not None and s.pk == selected.pk,
        })
        # Only chart periods that actually have a market decomposition.
        if rec["market_movement_impact"] is not None:
            recon_chart["labels"].append(label)
            recon_chart["operational"].append(float(rec["operational_growth"] or 0))
            recon_chart["market"].append(float(rec["market_movement_impact"] or 0))

    analytics = historical_analytics(history)

    context = {
        "snapshots": snapshots,
        "selected": selected,
        "editing": editing,
        "settings_obj": settings_obj,
        "history_rows": history_rows,
        "analytics": analytics,
        "today_iso": today.isoformat(),
        "has_data": bool(selected),
    }

    if selected:
        # Projection always anchors on the latest snapshot that has a closing AUM —
        # even if the user is viewing a historical period without AUM.
        projection_anchor = next(
            (s for s in history[::-1] if s.closing_aum is not None), None
        )
        dash = build_dashboard(
            selected, settings_obj, horizon_months=120,
            projection_anchor=projection_anchor,
        )
        context["dash"] = dash
        context["charts_json"] = json.dumps(dash["charts"]) if dash["charts"] else "null"
        context["recon"] = reconcile(selected, prev_by_pk.get(selected.pk))
        context["recon_chart_json"] = json.dumps(recon_chart)
        # Historical trajectory: skip periods missing closing AUM so the chart isn't broken.
        history_chart = {
            "labels": [h["label"] for h in history_rows if h["closing_aum"] is not None],
            "aum": [float(h["closing_aum"]) for h in history_rows if h["closing_aum"] is not None],
            "sip_book": [float(h["active_sip_book"]) for h in history_rows if h["closing_aum"] is not None],
            "trail": [float(h["trail_income"]) for h in history_rows if h["closing_aum"] is not None],
        }
        context["history_chart_json"] = json.dumps(history_chart)

    return render(request, "reports/mf_revenue_engine.html", context)

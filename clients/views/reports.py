"""Reports views: past performance, monthly business report."""
import json
from datetime import date
from decimal import Decimal
from calendar import month_name

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.db.models import Sum
from django.utils.timezone import now

from ..models import Sale, Employee, MonthlyTargetHistory
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

    labels = []
    totals_data = []
    months_data = []

    for y, m in months_for_year:
        label = f"{month_name[m]} {y}"
        sale_filter = {"date__year": y, "date__month": m}
        if selected_employee:
            sale_filter["employee"] = selected_employee

        total_points = Sale.objects.filter(**sale_filter).aggregate(total=Sum("points"))["total"] or 0
        total_amount = Sale.objects.filter(**sale_filter).aggregate(total=Sum("amount"))["total"] or 0

        totals_data.append(int(total_points))
        months_data.append({
            "year": y,
            "month": m,
            "label": label,
            "points": int(total_points),
            "amount": float(total_amount),
        })
        labels.append(label)

    max_points = max(totals_data) if totals_data else 0
    for md in months_data:
        md["percent_of_max"] = round((md["points"] / max_points) * 100, 1) if max_points else 0

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
        "labels_json": json.dumps(labels),
        "totals_json": json.dumps(totals_data),
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

    context = {
        "year": int(year),
        "month": int(month),
        "month_label": f"{month_name[int(month)]} {year}",
        "products": products,
        "top_performers": top_performers,
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

    products = ["SIP", "Lumsum", "Life Insurance", "Health Insurance", "Motor Insurance", "PMS", "COB"]
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__first_name")

    approved = Sale.objects.filter(status="approved", date__year=sel_year, date__month=sel_month)

    rows = []
    grand = {p: Decimal("0") for p in products}
    grand["points"] = Decimal("0")

    for e in employees:
        emp_sales = approved.filter(employee=e)
        product_vals = []
        for p in products:
            total = emp_sales.filter(product=p).aggregate(t=Sum("amount"))["t"] or Decimal("0")
            product_vals.append(total)
            grand[p] += total
        pts = emp_sales.aggregate(t=Sum("points"))["t"] or Decimal("0")
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

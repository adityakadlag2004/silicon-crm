"""Target resolution + month-close, shared by dashboards and the cron.

Targets are per-employee and per-head: each employee may carry a different
monthly target for a product (set on :class:`EmployeeTarget`), reflecting
their competency/skill level. Where an employee has no explicit row for a
product, we fall back to the product-wide baseline on :class:`Target`.

Daily targets are *derived* from the monthly target by dividing across the
working days of the month, matching the "monthly target divided into daily"
model rather than storing daily values independently.

``close_month()`` writes the month's performance-vs-target history and is the
single implementation — the ``close_month`` management command (CRONJOBS,
1st of every month) is a thin wrapper around it.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from ..models import Employee, EmployeeTarget, MonthlyTargetHistory, Sale, Target


def target_employees():
    """Active employees that carry personal targets, ordered by name.

    The organisation-wide product total on the dashboard is the sum of these
    employees' monthly targets.
    """
    return (
        Employee.objects.filter(active=True)
        .select_related("user")
        .order_by("user__username")
    )


def working_days_in_month(year, month):
    """Number of weekdays (Mon–Fri) in the given month.

    Used as the divisor when splitting a monthly target into daily targets.
    """
    _, last_day = monthrange(year, month)
    count = 0
    for day in range(1, last_day + 1):
        # weekday(): Mon=0 .. Sun=6 -> count Mon–Fri
        if date(year, month, day).weekday() < 5:
            count += 1
    return count or 1


def baseline_monthly_map():
    """Product name -> product-wide monthly baseline target."""
    return {
        t.product: (t.target_value or Decimal("0"))
        for t in Target.objects.filter(target_type="monthly")
    }


def employee_target_map(employees=None):
    """Return {(employee_id, product): monthly_value} for explicit per-employee targets.

    Pass ``employees`` (iterable of Employee or ids) to scope the lookup.
    """
    qs = EmployeeTarget.objects.all()
    if employees is not None:
        ids = [getattr(e, "id", e) for e in employees]
        qs = qs.filter(employee_id__in=ids)
    return {
        (et.employee_id, et.product): (et.target_value or Decimal("0"))
        for et in qs
    }


def resolve_monthly_target(employee_id, product, *, emp_map, baseline_map=None):
    """Monthly target for an employee+product: the employee's own per-product
    target, or 0. Targets are per-employee only — there is no product-wide
    baseline (a blank cell means "no target"). `baseline_map` is accepted and
    ignored for backwards compatibility with existing callers."""
    return emp_map.get((employee_id, product), Decimal("0"))


def resolve_daily_target(employee_id, product, working_days, *, emp_map, baseline_map):
    """Daily target = resolved monthly target / working days in the month."""
    monthly = resolve_monthly_target(employee_id, product, emp_map=emp_map, baseline_map=baseline_map)
    wd = working_days if working_days and working_days > 0 else 1
    return (monthly or Decimal("0")) / Decimal(wd)


def close_month(year, month, *, log=None):
    """Store every active employee's performance vs target for year/month.

    Uses the same per-employee target resolution as the dashboards
    (EmployeeTarget override, else product baseline), so the recorded history
    matches what employees saw during the month. Records both achieved amount
    and points. Idempotent — re-running updates the same rows.

    Returns the number of history rows written.
    """
    log = log or (lambda msg: None)
    emp_map = employee_target_map()

    written = 0
    for emp in Employee.objects.filter(active=True).select_related("user"):
        month_sales = (
            Sale.objects.filter(employee=emp, date__year=year, date__month=month)
            .values("product")
            .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        )
        sales_by_product = {s["product"]: s for s in month_sales}

        # Record every product this employee has a target for OR sold in — so
        # both their per-employee targets and their actual sales are captured.
        products = {prod for (eid, prod) in emp_map if eid == emp.id} | set(sales_by_product)

        for product in products:
            row = sales_by_product.get(product)
            achieved_amount = row["total_amount"] if row else 0
            achieved_points = row["total_points"] if row else 0
            target_value = resolve_monthly_target(emp.id, product, emp_map=emp_map)
            MonthlyTargetHistory.objects.update_or_create(
                employee=emp,
                product=product,
                year=year,
                month=month,
                defaults={
                    "target_value": target_value,
                    "achieved_value": achieved_amount,
                    "points_value": achieved_points,
                },
            )
            written += 1
            log(
                f"{emp.user.username} | {product}: "
                f"Achieved ₹{achieved_amount}, {achieved_points} pts "
                f"vs Target ₹{target_value}"
            )
    return written

"""Target resolution helpers.

Targets are per-employee and per-head: each employee may carry a different
monthly target for a product (set on :class:`EmployeeTarget`), reflecting their
competency/skill level. Where an employee has no explicit row for a product, we
fall back to the product-wide baseline on :class:`Target`.

Daily targets are *derived* from the monthly target by dividing across the
working days of the month, matching the "monthly target divided into daily"
model rather than storing daily values independently.
"""
from calendar import monthrange
from decimal import Decimal

from .models import Target, EmployeeTarget, Employee


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
        from datetime import date as _date
        if _date(year, month, day).weekday() < 5:
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


def resolve_monthly_target(employee_id, product, *, emp_map, baseline_map):
    """Monthly target for an employee+product, falling back to the baseline."""
    if (employee_id, product) in emp_map:
        return emp_map[(employee_id, product)]
    return baseline_map.get(product, Decimal("0"))


def resolve_daily_target(employee_id, product, working_days, *, emp_map, baseline_map):
    """Daily target = resolved monthly target / working days in the month."""
    monthly = resolve_monthly_target(employee_id, product, emp_map=emp_map, baseline_map=baseline_map)
    wd = working_days if working_days and working_days > 0 else 1
    return (monthly or Decimal("0")) / Decimal(wd)

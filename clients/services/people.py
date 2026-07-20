"""People care: milestones, celebrations, and profile completeness.

The premise is that keeping a team is mostly non-financial — being noticed
matters more often than being paid more. So the CRM treats recognition like
any other piece of work: it generates the occasions, surfaces the ones coming
up, and nags when one slips past uncelebrated.

Date-driven milestones (birthday, work anniversary, long service) are derived
from the Employee record; achievements are entered by hand.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from ..models import Employee, EmployeeMilestone

# Anniversaries worth calling out specially rather than as "N years".
LONG_SERVICE_YEARS = (1, 3, 5, 10, 15, 20, 25)

# How far ahead the dashboard looks, and how far back a missed one still nags.
UPCOMING_DAYS = 30
OVERDUE_GRACE_DAYS = 14


def _anniversary_in_year(when: date, year: int) -> date | None:
    """`when` moved to `year`, or None if it does not exist there.

    29 February only exists in leap years; rather than silently shifting a
    birthday to the 28th or 1st, we skip it and pick it up next leap year.
    """
    try:
        return when.replace(year=year)
    except ValueError:
        return None


def generate_for(employee: Employee, today: date | None = None) -> list[EmployeeMilestone]:
    """Create this year's date-driven milestones for one employee.

    Idempotent: unique_together on (employee, kind, occurs_on) means running
    it repeatedly does nothing. Returns only the rows actually created.
    """
    today = today or timezone.localdate()
    created = []

    if employee.date_of_birth:
        for year in (today.year, today.year + 1):
            occurs = _anniversary_in_year(employee.date_of_birth, year)
            if not occurs or not (0 <= (occurs - today).days <= 365):
                continue
            row, made = EmployeeMilestone.objects.get_or_create(
                employee=employee, kind=EmployeeMilestone.Kind.BIRTHDAY, occurs_on=occurs,
                defaults={"title": f"{employee.short_name}'s birthday"},
            )
            if made:
                created.append(row)

    if employee.joining_date:
        for year in (today.year, today.year + 1):
            occurs = _anniversary_in_year(employee.joining_date, year)
            if not occurs or occurs <= employee.joining_date:
                continue
            if not (0 <= (occurs - today).days <= 365):
                continue
            years = occurs.year - employee.joining_date.year
            long_service = years in LONG_SERVICE_YEARS
            kind = (EmployeeMilestone.Kind.LONG_SERVICE if long_service
                    else EmployeeMilestone.Kind.WORK_ANNIVERSARY)
            label = "years" if years != 1 else "year"
            row, made = EmployeeMilestone.objects.get_or_create(
                employee=employee, kind=kind, occurs_on=occurs,
                defaults={
                    "title": f"{employee.short_name} — {years} {label} with the firm",
                    "years": years,
                },
            )
            if made:
                created.append(row)
    return created


def generate_all(today: date | None = None) -> list[EmployeeMilestone]:
    """Generate date-driven milestones for every active employee."""
    created = []
    for emp in Employee.objects.filter(active=True).select_related("user"):
        created += generate_for(emp, today)
    return created


def upcoming(days: int = UPCOMING_DAYS):
    """Milestones landing in the next `days`, soonest first."""
    today = timezone.localdate()
    return (
        EmployeeMilestone.objects.filter(
            employee__active=True,
            occurs_on__gte=today,
            occurs_on__lte=today + timedelta(days=days),
        )
        .select_related("employee__user")
        .order_by("occurs_on")
    )


def missed(grace_days: int = OVERDUE_GRACE_DAYS):
    """Recently passed milestones nobody marked — the nudge list.

    Bounded by `grace_days` so an old backlog doesn't shout forever; the point
    is to catch the one you missed last week, not guilt-trip about last year.
    """
    today = timezone.localdate()
    return (
        EmployeeMilestone.objects.filter(
            employee__active=True,
            celebrated_at__isnull=True,
            occurs_on__lt=today,
            occurs_on__gte=today - timedelta(days=grace_days),
        )
        .select_related("employee__user")
        .order_by("occurs_on")
    )


def incomplete_profiles():
    """Active employees whose self-service profile still has gaps."""
    out = []
    for emp in Employee.objects.filter(active=True).select_related("user"):
        missing = emp.missing_fields()
        if missing:
            out.append({"employee": emp, "missing": missing,
                        "percent": emp.profile_completeness})
    out.sort(key=lambda r: r["percent"])
    return out


def celebrate(milestone: EmployeeMilestone, by_user, note: str = ""):
    """Mark a milestone celebrated and tell the person they were noticed."""
    milestone.celebrated_at = timezone.now()
    milestone.celebrated_by = by_user
    milestone.celebration_note = note
    milestone.save(update_fields=["celebrated_at", "celebrated_by",
                                  "celebration_note"])

    # The recognition is the point — make sure it actually reaches them.
    from .tasks import create_notification
    body = note or f"The team is marking: {milestone.title}"
    create_notification(
        milestone.employee.user,
        f"🎉 {milestone.get_kind_display()}",
        body,
        link="",
        event=None,
    )
    return milestone

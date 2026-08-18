"""One common calendar: a unified feed of everything dated in the CRM.

Aggregates every dated item an employee cares about into one normalized
shape so all calendar surfaces show the same things:

  - the FullCalendar page  (/calendar/view/  → calendar_events_json)
  - the dashboard agenda widget (admin + employee dashboards)

Sources:
  event          CalendarEvent — manual events created on the calendar
  birthday       Client birthdays (clients mapped to the employee)
  call_followup  CallFollowUp   — pending follow-ups from call tracking
  task           Task           — open tasks with a due date, which is also
                                  every lead/claim follow-up (a follow-up is a
                                  Task — see services/followups.py)
  insurance_renewal  Sale       — annual policy renewal, on the policy-date
                                  anniversary (Health/Life insurance)

Each item is a plain dict:
  key          "lead-42" — source-prefixed, unique across the feed
  source       one of the source names above
  title / note / assigned_to
  start / end  aware datetimes (end may be None)
  status       "pending" | "missed" | CalendarEvent statuses
  url          page that shows/handles the item
  done_url     POST target that completes the item (None = no quick action)
  draggable    whether the dashboard widget may drag-reschedule it
"""
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from ..models import CalendarEvent, CallFollowUp, Client, Lead, Sale, Task

ALL_SOURCES = ("event", "birthday", "call_followup", "task", "insurance_renewal")

SOURCE_LABELS = {
    "event": "Event",
    "birthday": "Birthday",
    "call_followup": "Call",
    "task": "Task",
    "insurance_renewal": "Renewal",
}


def _item(key, source, title, start, *, end=None, note="", status="pending",
          url="", done_url=None, draggable=False, assigned_to="", event_type=None,
          source_label=None, stage=None):
    return {
        "key": key,
        "source": source,
        # A lead/claim follow-up IS a task, so it shares the "task" source (and
        # its filter chip) but says which kind of commitment it is on the badge.
        "source_label": source_label or SOURCE_LABELS[source],
        # SPANCO stage, for lead follow-ups only — the agenda tints and ranks by it
        "stage": stage,
        "title": title,
        "start": start,
        "end": end,
        "note": note or "",
        "status": status,
        "url": url,
        "done_url": done_url,
        "draggable": draggable,
        "assigned_to": assigned_to,
        # what the calendar page's type filter/colour map keys off
        "event_type": event_type or source,
    }


def _events(employee, start, end):
    qs = CalendarEvent.objects.filter(employee=employee)
    if start:
        qs = qs.filter(scheduled_time__gte=start)
    if end:
        qs = qs.filter(scheduled_time__lte=end)
    now_ts = timezone.now()
    items = []
    for e in qs:
        status = e.status
        if status == "pending" and e.scheduled_time and e.scheduled_time < now_ts:
            status = "missed"
        items.append(_item(
            f"event-{e.id}", "event", e.title, e.scheduled_time,
            end=e.end_time, note=e.notes, status=status,
            url=reverse("clients:employee_calendar_page"),
            done_url=reverse("clients:mark_done", args=[e.id]),
            event_type=e.type,
        ))
    return items


def _birthdays(employee, start, end):
    """Expand client birthdays into per-year items inside [start, end]."""
    items = []
    for client in Client.objects.filter(date_of_birth__isnull=False, mapped_to=employee):
        dob = client.date_of_birth
        for yr in range(start.year, end.year + 1):
            try:
                bday = date(yr, dob.month, dob.day)
            except ValueError:  # 29 Feb in a non-leap year
                continue
            bday_dt = timezone.make_aware(datetime.combine(bday, time.min))
            if start <= bday_dt <= end:
                items.append(_item(
                    f"birth-{client.id}-{yr}", "birthday",
                    f"Birthday: {client.name}", bday_dt,
                    note="Auto-generated birthday call",
                    url=reverse("clients:client_profile", args=[client.id]),
                ))
    return items


def _call_followups(employee, start, end, employee_id=None):
    qs = CallFollowUp.objects.filter(status=CallFollowUp.STATUS_PENDING).select_related(
        "client", "employee__user")
    if employee is not None:
        qs = qs.filter(employee=employee)
    elif employee_id:
        qs = qs.filter(employee_id=employee_id)
    if start:
        qs = qs.filter(scheduled_at__gte=start)
    if end:
        qs = qs.filter(scheduled_at__lte=end)
    return [
        _item(
            f"call-{f.id}", "call_followup",
            f"Call {f.client.name if f.client_id else f.phone}", f.scheduled_at,
            note=f.note,
            url=reverse("clients:my_call_followups"),
            assigned_to=f.employee.user.username if f.employee.user_id else "",
        )
        for f in qs
    ]


# A lead/claim follow-up is a Task, so the badge has to say which — otherwise
# every commitment on the agenda reads "Task" and the pipeline is invisible.
TASK_KIND_LABELS = {"lead": "Lead", "claim": "Claim"}


def _tasks(employee, start, end, employee_id=None):
    """Open dated tasks — which is also every lead and claim follow-up.

    Scoped exactly like `_call_followups`: an employee sees their own, an admin
    dashboard (employee=None) sees the whole team's, optionally narrowed to one
    person. Before follow-ups became tasks, lead follow-ups honoured team scope
    on their own; hard-scoping this to one employee is what made the admin
    agenda stop showing the pipeline.
    """
    qs = Task.objects.filter(
        is_deleted=False,
        status__in=Task.OPEN_STATUSES,
        due_date__isnull=False,
    ).select_related("assigned_to__user")
    if employee is not None:
        qs = qs.filter(assigned_to=employee)
    elif employee_id:
        qs = qs.filter(assigned_to_id=employee_id)
    if start:
        qs = qs.filter(due_date__gte=start.date())
    if end:
        qs = qs.filter(due_date__lte=end.date())
    qs = list(qs)

    # One extra query for every lead behind a follow-up, so the agenda can tint
    # and rank by SPANCO stage — a Negotiation chase is not a stationery order.
    lead_ids = {t.source_id for t in qs if t.source_kind == "lead" and t.source_id}
    lead_stages = dict(
        Lead.objects.filter(id__in=lead_ids, is_discarded=False).values_list("id", "stage")
    ) if lead_ids else {}

    items = []
    for t in qs:
        # date-only tasks are "due by end of day"
        due_dt = timezone.make_aware(datetime.combine(t.due_date, t.due_time or time(23, 59)))
        items.append(_item(
            f"task-{t.id}", "task", t.title, due_dt,
            note=t.description[:120] if t.description else "",
            url=reverse("clients:task_detail", args=[t.id]),
            done_url=reverse("clients:task_set_status", args=[t.id]),
            draggable=True,
            assigned_to=(t.assigned_to.user.username
                         if t.assigned_to and t.assigned_to.user_id else ""),
            source_label=TASK_KIND_LABELS.get(t.source_kind),
            stage=lead_stages.get(t.source_id) if t.source_kind == "lead" else None,
        ))
    return items


def _insurance_renewals(employee, start, end):
    """Expand Health/Life insurance policy renewals into per-year items.

    The anniversary is measured from the policy's commencement date
    (``Sale.policy_date``), read off the policy document — never the sale
    (approval) date, which legacy rows without a policy_date still fall back
    to. Only approved sales generate a renewal reminder.
    """
    # The employee who OWNS the client relationship handles the renewal call, so
    # the reminder lands on the mapped employee's calendar (falling back to the
    # seller when the client isn't mapped).
    qs = Sale.objects.filter(
        status=Sale.STATUS_APPROVED,
    ).select_related("client", "product_ref").filter(
        Q(client__mapped_to=employee)
        | (Q(employee=employee) & Q(client__mapped_to__isnull=True))
    ).filter(
        Q(product_ref__code__in=["HEALTH_INS", "LIFE_INS"])
        | Q(product__iexact="Health Insurance")
        | Q(product__iexact="Life Insurance")
    )
    items = []
    for sale in qs:
        basis = sale.renewal_basis
        if not basis:
            continue
        # A multiyear policy is paid through its term — no renewal until it ends.
        coverage_end = sale.coverage_end() or basis
        for yr in range(start.year, end.year + 1):
            try:
                anniv = date(yr, basis.month, basis.day)
            except ValueError:                 # 29 Feb → 28 Feb in common years
                anniv = date(yr, basis.month, 28)
            # Only renewals due at/after the paid term ends (skips the years a
            # multiyear policy already covers, and the commencement year).
            if anniv < coverage_end:
                continue
            anniv_dt = timezone.make_aware(datetime.combine(anniv, time(10, 0)))
            if not (start <= anniv_dt <= end):
                continue
            label = sale.product_ref.name if sale.product_ref_id else sale.product
            items.append(_item(
                f"insrenew-{sale.id}-{yr}", "insurance_renewal",
                f"{label} renewal: {sale.client.name}", anniv_dt,
                note=f"Policy started {basis:%d %b %Y}. Confirm renewal with the client.",
                url=reverse("clients:client_profile", args=[sale.client.id]),
            ))
    return items


def feed_items(employee, *, start=None, end=None, sources=None,
               team_followups=False, employee_id=None):
    """Collect unified calendar items.

    employee        scope for personal sources (events, birthdays, renewals)
                    and — unless team_followups — for tasks and calls too.
    team_followups  admin dashboards: include *all* employees' tasks (every
                    lead/claim follow-up is one) and call follow-ups,
                    optionally narrowed to employee_id.
    """
    sources = [s for s in (sources or ALL_SOURCES) if s in ALL_SOURCES]
    # birthdays must have a bounded range to expand years into
    b_start = start or timezone.now() - timedelta(days=365)
    b_end = end or timezone.now() + timedelta(days=365)

    fu_employee = None if team_followups else employee

    items = []
    if "event" in sources:
        items += _events(employee, start, end)
    if "birthday" in sources:
        items += _birthdays(employee, b_start, b_end)
    if "call_followup" in sources:
        items += _call_followups(fu_employee, start, end, employee_id=employee_id)
    if "task" in sources:
        items += _tasks(fu_employee, start, end, employee_id=employee_id)
    if "insurance_renewal" in sources:
        items += _insurance_renewals(employee, b_start, b_end)

    now_ts = timezone.now()
    for it in items:
        # a birthday in the past is history, not an overdue action item
        it["is_overdue"] = (
            it["source"] != "birthday"
            and it["status"] in ("pending", "missed")
            and it["start"] < now_ts
        )
    # Within a day, a live-pipeline follow-up sorts above the rest: the whole
    # point of the stage is to say which chase moves money.
    items.sort(key=lambda it: (
        timezone.localdate(it["start"]),
        0 if it["stage"] in Lead.STAGE_HOT else 1,
        it["start"],
    ))
    return items


def to_fullcalendar(items):
    """Map feed items to FullCalendar event objects.

    Only manual CalendarEvents are editable in place; everything else is
    read-only on the calendar and links out to its own page via
    extendedProps.url.
    """
    out = []
    for it in items:
        is_manual = it["source"] == "event"
        out.append({
            # manual events keep their integer id — the edit/delete endpoints
            # and the page JS depend on it
            "id": it["key"].split("-", 1)[1] if is_manual else it["key"],
            "title": it["title"],
            "start": it["start"].isoformat(),
            "end": it["end"].isoformat() if it["end"] else None,
            "editable": is_manual,
            "extendedProps": {
                "type": it["event_type"],
                "notes": it["note"],
                "status": it["status"],
                "source": it["source"],
                "source_label": it["source_label"],
                "stage_color": Lead.STAGE_COLORS.get(it["stage"], ""),
                "url": "" if is_manual else it["url"],
                "assigned_to": it["assigned_to"],
            },
        })
    return out


def to_agenda_json(items):
    """Map feed items to the dashboard agenda widget's JSON shape."""
    out = []
    for it in items:
        local = timezone.localtime(it["start"])
        out.append({
            "key": it["key"],
            "source": it["source"],
            "source_label": it["source_label"],
            "stage": it["stage"] or "",
            "stage_label": dict(Lead.STAGE_CHOICES).get(it["stage"], ""),
            "stage_color": Lead.STAGE_COLORS.get(it["stage"], ""),
            "hot": it["stage"] in Lead.STAGE_HOT,
            "title": it["title"],
            "date": local.strftime("%Y-%m-%d"),
            "time": local.strftime("%H:%M"),
            "note": it["note"],
            "assigned_to": it["assigned_to"],
            "is_overdue": it["is_overdue"],
            "url": it["url"],
            "done_url": it["done_url"],
            "draggable": it["draggable"],
            # raw id for the widget's drag-reschedule (lead follow-ups only)
            "followup_id": int(it["key"].rsplit("-", 1)[1]) if it["draggable"] else None,
        })
    return out

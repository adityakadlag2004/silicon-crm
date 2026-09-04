"""Client alerts driven by the date of birth: the birthday call, and turning 40.

Both answer the same shape of question — "this client's birth date means
somebody should do something today" — so they share one task-raising
implementation and differ only in what they say:

  * **Birthday** (`birthdays_today`): every year, on the day. Wish them, send
    the valuation report, review the financial plan. Medium priority — a
    birthday is not an alarm-clock emergency, and HIGH re-rings every four
    hours until acknowledged, which is how a daily task becomes noise people
    swipe away.
  * **Retirement planning** (`turning_age_today`): once, at 40, when retirement
    planning stops being theoretical. High priority — it happens once per
    client per lifetime.

Both are assigned to the client's **mapped employee and every active admin**,
as one multi-assignee group (sibling `Task` rows sharing `assign_group`), so
each person owns their own status. That group id is also the dedup key, so a
re-run — or two commands overlapping — can never raise the work twice.
``Task.assign_group`` is 32 chars: keep the prefixes short.
"""
from __future__ import annotations

from datetime import date, time

from django.utils import timezone

from ..models import Client, Employee, Task, TaskCategory, TaskChecklistItem
from .tasks import ring_task

# The age that triggers the retirement conversation.
TRIGGER_AGE = 40
# Both are daytime work, and Task.due_at needs a time or tasks_ring_due skips
# the task and the phone's on-device alarm never arms.
BIRTHDAY_TIME = time(10, 0)
RETIREMENT_TIME = time(11, 0)

CATEGORY_NAME = "Client Care"


def category():
    """Generated client alerts land here so the real task list stays readable —
    the same reasoning as followups' "Follow-up" category."""
    cat, _ = TaskCategory.objects.get_or_create(
        name=CATEGORY_NAME,
        defaults={"color": "#DB2777", "icon": "bi-gift",
                  "description": "Auto-generated birthday and life-stage alerts."},
    )
    return cat


# ───────────────────────────── shared plumbing ─────────────────────────────

def assignees(client):
    """The mapped employee first, then every active admin, de-duplicated.

    Admins get their own row rather than being subscribed: the owner asked for
    the task to be *allocated* to them, and a sibling row is what lets each
    person close their own copy.
    """
    people = []
    seen = set()
    emp = client.mapped_to
    if emp and emp.user_id:
        people.append(emp)
        seen.add(emp.pk)
    for admin in Employee.objects.filter(active=True, role="admin").select_related("user"):
        if admin.user_id and admin.pk not in seen:
            people.append(admin)
            seen.add(admin.pk)
    return people


def already_raised(key) -> bool:
    return Task.objects.filter(assign_group=key, is_deleted=False).exists()


def _raise(client, *, key, title, description, due_date, due_time, priority,
           checklist=(), ring_title="", ring_body="", ring=True):
    """Create the task group for `client`, or do nothing if `key` already exists.

    Returns (tasks, created).
    """
    if already_raised(key):
        return [], False

    people = assignees(client)
    cat = category()
    # Nobody to assign it to (unmapped client, no admins) still leaves the work
    # on the board rather than dropping it silently.
    targets = people or [None]
    tasks = []
    for emp in targets:
        task = Task.objects.create(
            title=title[:255], description=description, category=cat,
            assigned_to=emp, client=client,
            due_date=due_date, due_time=due_time,
            priority=priority, assign_group=key,
        )
        for i, item in enumerate(checklist):
            TaskChecklistItem.objects.create(task=task, title=item[:255], order=i)
        tasks.append(task)

    if ring and ring_title:
        for task in tasks:
            if task.assigned_to and task.assigned_to.user_id:
                ring_task(task.assigned_to.user, ring_title, ring_body, task)
    return tasks, True


def _run(pairs, ring=True):
    """`pairs` is an iterable of (client, raiser). Returns (created, skipped)."""
    created = skipped = 0
    for client, raise_one in pairs:
        _tasks, made = raise_one(client, ring=ring)
        created += 1 if made else 0
        skipped += 0 if made else 1
    return created, skipped


def _born_on_or_before(today: date, years: int) -> date:
    """The latest birth date that makes someone `years` old today."""
    try:
        return today.replace(year=today.year - years)
    except ValueError:                      # 29 Feb
        return today.replace(year=today.year - years, day=28)


# ───────────────────────────── birthday call ─────────────────────────────

BIRTHDAY_CHECKLIST = (
    "Wish them a happy birthday",
    "Send the valuation report",
    "Review the financial plan",
)


def birthday_key(client, year) -> str:
    return f"bday:{client.pk}:{year}"


def birthdays_today(today=None):
    """Clients whose birthday is today.

    29 February birthdays are wished on 28 February in a non-leap year —
    otherwise they are skipped three years in four.
    """
    today = today or timezone.localdate()
    qs = Client.objects.filter(date_of_birth__month=today.month,
                               date_of_birth__day=today.day)
    is_leap_year = (today.year % 4 == 0 and (today.year % 100 != 0 or today.year % 400 == 0))
    if (today.month, today.day) == (2, 28) and not is_leap_year:
        qs = Client.objects.filter(
            date_of_birth__month=2, date_of_birth__day__in=(28, 29))
    return qs.select_related("mapped_to__user")


def raise_birthday_task(client, *, today=None, ring=True):
    today = today or timezone.localdate()
    age = client.age
    turned = f" — turns {age} today" if age is not None else ""
    lines = [
        f"Today is {client.name}'s birthday{turned}.",
        "",
        f"Phone   : {client.phone or '—'}",
        f"Age     : {age if age is not None else '—'}",
        "",
        "On the call:",
        "  1. Wish them a happy birthday.",
        "  2. Send their valuation report.",
        "  3. Review their financial plan and flag anything that needs revisiting.",
        "",
        "Holdings on file:",
        f"  SIP          : ₹{client.sip_amount or 0:,.0f}",
        f"  Lumpsum      : ₹{client.lumsum_investment or 0:,.0f}",
        f"  PMS          : ₹{client.pms_amount or 0:,.0f}",
        f"  Life cover   : ₹{client.life_cover or 0:,.0f}",
        f"  Health cover : ₹{client.health_cover or 0:,.0f}",
    ]
    return _raise(
        client,
        key=birthday_key(client, today.year),
        title=f"Birthday · {client.name}{turned}",
        description="\n".join(lines),
        due_date=today, due_time=BIRTHDAY_TIME,
        # Medium on purpose: high/critical re-ring every four hours until
        # acknowledged, and a daily task at that volume is a storm.
        priority=Task.PRIORITY_MEDIUM,
        checklist=BIRTHDAY_CHECKLIST,
        ring_title=f"🎂 {client.name}'s birthday today",
        ring_body="Wish them, send the valuation report, review the plan.",
        ring=ring,
    )


def run_birthdays(clients, *, today=None, ring=True):
    today = today or timezone.localdate()
    return _run(((c, lambda c, ring: raise_birthday_task(c, today=today, ring=ring))
                 for c in clients), ring=ring)


# ────────────────────────── retirement planning ──────────────────────────

def retirement_key(client) -> str:
    return f"retire:{client.pk}"


def turning_age_today(today=None, age: int = TRIGGER_AGE):
    """Clients whose `age`-th birthday is today."""
    today = today or timezone.localdate()
    return (Client.objects.filter(date_of_birth=_born_on_or_before(today, age))
            .select_related("mapped_to__user"))


def retirement_backlog(today=None, age: int = TRIGGER_AGE):
    """Clients already past `age` who have never been alerted.

    The daily cron only ever sees today's birthdays, so this is how the book
    that predates the feature — and an old profile whose date of birth is typed
    in on KYC Issues years late — ever gets its alert.
    """
    today = today or timezone.localdate()
    alerted = set(
        Task.objects.filter(assign_group__startswith="retire:", is_deleted=False)
        .values_list("assign_group", flat=True)
    )
    qs = (Client.objects
          .filter(date_of_birth__isnull=False,
                  date_of_birth__lte=_born_on_or_before(today, age))
          .select_related("mapped_to__user").order_by("date_of_birth"))
    return [c for c in qs if retirement_key(c) not in alerted]


def raise_retirement_alert(client, *, today=None, ring=True):
    today = today or timezone.localdate()
    lines = [
        f"Client   : {client.name}",
        f"Age      : {client.age}",
        f"Born     : {client.date_of_birth:%d %b %Y}" if client.date_of_birth else "Born     : —",
        f"Phone    : {client.phone or '—'}",
        "",
        "Holdings on file:",
        f"  SIP          : ₹{client.sip_amount or 0:,.0f}",
        f"  Lumpsum      : ₹{client.lumsum_investment or 0:,.0f}",
        f"  PMS          : ₹{client.pms_amount or 0:,.0f}",
        f"  Life cover   : ₹{client.life_cover or 0:,.0f}",
        f"  Health cover : ₹{client.health_cover or 0:,.0f}",
        "",
        f"{client.name} has turned {TRIGGER_AGE}. Review the retirement corpus "
        "needed against what is already invested, run the financial plan, and "
        "propose a retirement plan.",
    ]
    return _raise(
        client,
        key=retirement_key(client),
        title=f"Start retirement planning · {client.name} (turned {TRIGGER_AGE})",
        description="\n".join(lines),
        due_date=today, due_time=RETIREMENT_TIME,
        priority=Task.PRIORITY_HIGH,
        ring_title=f"{client.name} has turned {TRIGGER_AGE}",
        ring_body="Start retirement planning — review the corpus and propose a plan.",
        ring=ring,
    )


def run_retirement(clients, *, today=None, ring=True):
    today = today or timezone.localdate()
    return _run(((c, lambda c, ring: raise_retirement_alert(c, today=today, ring=ring))
                 for c in clients), ring=ring)

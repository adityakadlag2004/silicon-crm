"""Chase the renewed policy document into Google Drive.

The insurer issues the renewed policy three or four days after the renewal is
entered, so the tick on the renewal (`Renewal.policy_doc_submitted`) is blank
on the day it is booked and nothing ever asked for it again — the Filed/Pending
column was the only place an unfiled renewal showed, and only if somebody went
looking.

Four days on, this raises one task for the employee who entered the renewal:
upload the policy to the client's Drive folder and tick the box. Medium
priority on purpose — high/critical re-ring every four hours until
acknowledged, and a filing reminder at that volume is a storm people learn to
swipe away.

It reads a small catch-up window (4–6 days old) rather than exactly the 4th
day, so a missed cron morning doesn't drop a renewal for good; the
``assign_group = "renupl:<renewal>"`` key means a re-run — or the same renewal
seen on three consecutive mornings — still raises the task once.

The chase stops by itself: ticking the box completes the open task
(``signals._close_renewal_upload_task``), so nobody has to close it by hand.

In CRONJOBS (daily 9:00 AM).
"""

from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Renewal, Task
from clients.services.client_alerts import category
from clients.services.tasks import create_notification
from clients.templatetags.custom_filters import inr

# Days after entry before the policy is chased, and how far back to look —
# the extra two days are the catch-up for a morning the cron did not run.
AFTER_DAYS = 4
CATCH_UP_DAYS = 2
# Task.due_at needs a time or tasks_ring_due skips the task entirely.
DUE_TIME = time(11, 0)


def task_key(renewal):
    """Short — Task.assign_group is 32 chars."""
    return f"renupl:{renewal.pk}"


def _owner(renewal):
    """Who is chased: whoever entered the renewal, then the client's employee.

    The entering employee is the one waiting on the insurer's document, which
    is why they are asked before the mapped employee.
    """
    if renewal.employee and renewal.employee.user_id:
        return renewal.employee
    by = getattr(renewal.created_by, "employee", None) if renewal.created_by_id else None
    if by and by.user_id:
        return by
    emp = renewal.client.mapped_to
    return emp if (emp and emp.user_id) else None


def _describe(renewal):
    policy = renewal.policy
    lines = [
        f"Client          : {renewal.client.name}",
        f"Policy no.      : {(policy.policy_number if policy else '') or '—'}",
        f"Insurer         : {(policy.insurer if policy else '') or '—'}",
        f"Product         : {renewal.product_name or (renewal.product_ref.name if renewal.product_ref else '—')}",
        f"Premium         : ₹{inr(renewal.premium_amount or 0)}",
        f"Renewal date    : {renewal.renewal_date:%d %b %Y}",
        f"Entered on      : {timezone.localtime(renewal.created_at):%d %b %Y}",
        "",
        "The renewed policy should have been issued by now. Upload it to the",
        "client's Google Drive folder (the link is on their profile), then tick",
        "“Renewal policy submitted to Google Drive” on the renewal — this task",
        "closes itself once you do.",
    ]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Remind the employee to file a renewed policy in Drive, 4 days after the renewal was entered."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=AFTER_DAYS,
                            help=f"Days after entry to chase (default {AFTER_DAYS}).")

    def handle(self, *args, **opts):
        today = timezone.localdate()
        newest = today - timedelta(days=opts["days"])
        oldest = newest - timedelta(days=CATCH_UP_DAYS)

        renewals = (
            Renewal.objects
            .filter(policy_doc_submitted=False,
                    created_at__date__gte=oldest, created_at__date__lte=newest)
            .select_related("client", "client__mapped_to__user", "employee__user",
                            "created_by", "policy", "product_ref")
        )

        # Generated client work already has a home — the same "Client Care"
        # category the birthday and retirement alerts land in.
        cat = category()

        made = skipped = 0
        for renewal in renewals:
            key = task_key(renewal)
            if Task.objects.filter(assign_group=key, is_deleted=False).exists():
                skipped += 1
                continue
            emp = _owner(renewal)
            if not emp:
                skipped += 1
                continue
            task = Task.objects.create(
                title=f"Upload renewed policy · {renewal.client.name}"[:255],
                description=_describe(renewal),
                category=cat, assigned_to=emp, client=renewal.client,
                due_date=today, due_time=DUE_TIME,
                priority=Task.PRIORITY_MEDIUM, assign_group=key,
            )
            create_notification(
                emp.user,
                f"Upload the renewed policy for {renewal.client.name}",
                f"Entered {(today - renewal.created_at.date()).days} days ago and still "
                f"not filed in Drive.",
                link=f"/clients/tasks/{task.pk}/",
                ring=False,
            )
            made += 1

        self.stdout.write(self.style.SUCCESS(
            f"Renewal upload reminders: {made} task(s) created, {skipped} skipped."
        ))

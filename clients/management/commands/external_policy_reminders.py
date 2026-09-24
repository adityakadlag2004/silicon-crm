"""Reminders for the policies our clients hold elsewhere (External Policies).

Runs daily. Every dated event on a live external policy — a premium or renewal
due, a money-back, the maturity, the pension vesting — becomes one task for the
client's **mapped employee** (fallback: whoever added the policy), due on the
event date, raised as soon as the event is inside 30 days and rung again a week
before. Every task is titled "External Policy · …" and says so in its first
line, so nobody mistakes it for a premium we collect.

Reading a window rather than an exact day means a missed morning, or a policy
added ten days before its premium, still gets its task. Deduped by
``Task.assign_group = "xpol:<policy>:<kind>:<yyyymmdd>"`` (32-char field).

Quarterly premiums are reminded 7 days out, not 30 — a 30-day lead would land
on top of the previous quarter's. Monthly premiums get none: insurers only take
monthly mode on auto-debit, and twelve tasks a year for money that moves by
itself is noise.

In CRONJOBS (daily 8:50).
"""

from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from clients.models import ExternalPolicy, Task
from clients.services import client_alerts, followups
from clients.services.tasks import ring_task
from clients.templatetags.custom_filters import inr

LEAD_DAYS = 30
SHORT_LEAD_DAYS = 7
RING_AGAIN_DAYS = 7
DUE_TIME = time(10, 0)

ACTION = {
    "premium": "Remind the client the premium is due, so the policy does not lapse.",
    "renewal": "Remind the client to renew before the cover lapses — and review whether it is still enough.",
    "payout": "A money-back payout reaches the client — talk about where to put it to work.",
    "maturity": "The policy matures — plan with the client what to do with the maturity amount.",
    ExternalPolicy.TYPE_PENSION: "Pension vesting — help the client pick the annuity option so the pension starts on time.",
    ExternalPolicy.TYPE_TERM: "The life cover ends here — the client is unprotected after this date; review their cover.",
}


def _lead_days(policy, event):
    """Days ahead the task is raised; None = never (monthly premiums)."""
    if event["kind"] != "premium" or policy.premium_frequency >= 6:
        return LEAD_DAYS
    return SHORT_LEAD_DAYS if policy.premium_frequency == 3 else None


def _describe(policy, event):
    client = policy.client
    lines = [
        "EXTERNAL POLICY — the client holds this policy; it was NOT sold by us.",
        "",
        f"{event['label']:<16}: {event['date']:%d %b %Y}"
        + (f" · ₹{inr(event['amount'])}" if event["amount"] else ""),
        f"Policy no.      : {policy.policy_number or '—'}",
        f"Type            : {policy.get_policy_type_display()}",
        f"Insurer / plan  : {policy.insurer}" + (f" · {policy.plan_name}" if policy.plan_name else ""),
        f"Sum assured     : ₹{inr(policy.sum_assured or 0)}",
        f"Premium         : ₹{inr(policy.premium_amount or 0)} "
        + ("per renewal" if policy.is_renewable else policy.get_premium_frequency_display().lower()),
        f"Commenced       : {policy.start_date:%d %b %Y}",
    ]
    if policy.maturity_date:
        lines.append(f"{policy.maturity_label:<16}: {policy.maturity_date:%d %b %Y}"
                     + (f" · ₹{inr(policy.maturity_amount)} expected" if policy.maturity_amount else ""))
    lines += [
        "",
        f"Client          : {client.name}",
        f"Phone           : {client.phone or '—'}",
        f"Nominee         : {policy.nominee_name or '—'}",
        "",
        ACTION.get(policy.policy_type if event["kind"] == "maturity" else "", ACTION[event["kind"]]),
        reverse("clients:external_policy_detail", args=[policy.pk]),
    ]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Reminder tasks for premiums, renewals, money-backs and maturities on external policies."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        policies = (ExternalPolicy.objects
                    .filter(status__in=ExternalPolicy.LIVE_STATUSES)
                    .select_related("client__mapped_to__user", "created_by__employee__user"))
        made = rung = 0
        for policy in policies:
            emp = policy.client.mapped_to or getattr(policy.created_by, "employee", None)
            for event in policy.events(today, today + timedelta(days=LEAD_DAYS)):
                days = (event["date"] - today).days
                lead = _lead_days(policy, event)
                if lead is None or days > lead:
                    continue
                key = f"xpol:{policy.pk}:{event['kind']}:{event['date']:%Y%m%d}"
                task = Task.objects.filter(assign_group=key, is_deleted=False).first()
                if task is None:
                    task = Task.objects.create(
                        title=(f"External Policy · {event['label']} · {policy.client.name} · "
                               f"{policy.insurer} {policy.policy_number}").strip()[:255],
                        description=_describe(policy, event),
                        category=client_alerts.category(),
                        created_by=followups.system_user(),
                        assigned_to=emp, client=policy.client,
                        due_date=event["date"], due_time=DUE_TIME,
                        priority=Task.PRIORITY_MEDIUM, assign_group=key,
                        source_kind=ExternalPolicy.TASK_SOURCE, source_id=policy.pk,
                    )
                    made += 1
                elif days != RING_AGAIN_DAYS or task.status not in Task.OPEN_STATUSES:
                    continue
                if emp and emp.user_id:
                    ring_task(
                        emp.user,
                        f"External policy · {event['label']} in {days} days — {policy.client.name}",
                        f"{policy.insurer} {policy.policy_number} · {event['date']:%d %b}"
                        + (f" · ₹{inr(event['amount'])}" if event["amount"] else ""),
                        task,
                    )
                    rung += 1

        self.stdout.write(self.style.SUCCESS(
            f"External policy reminders: {made} task(s) created, {rung} alert(s) rung."))

"""Renewal reminders for every policy on the Insurance Tracker.

Runs daily. A policy's ``end_date`` *is* its renewal due date, so this walks
the tracker rather than the sales book — that is the only way the old book
reaches an employee's phone, since those policies were back-filled from a
renewal entry and have no sale behind them.

At a month (30 days) and a week (7 days) before the due date it assigns the
client's **mapped employee** one high-priority "Renewal Reminder" task, due on
the renewal date, and rings their phone. A collected renewal rolls the policy's
``end_date`` forward (``insurance_sync._advance_cover``), so the policy leaves
the window by itself and the chasing stops — nobody has to close the task to
silence it.

In CRONJOBS (daily 8:45). One task per policy per renewal cycle, deduped via
``Task.assign_group = "polrenew:<policy>:<due-date>"`` (32-char field).
"""

from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import InsurancePolicy, Task
from clients.services.tasks import ring_task
from clients.templatetags.custom_filters import inr

# A month before, then a week before. Both ring.
REMIND_DAYS = (30, 7)
# Renewal calls are a morning job; this is also when the phone's on-device
# alarm goes off on the due date itself (Task.due_at needs a time to arm).
DUE_TIME = time(10, 0)


def _describe(policy, last_renewal):
    """Everything the employee needs to make the call, in the task body."""
    client = policy.client
    lines = [
        f"Policy no.      : {policy.policy_number or '—'}",
        f"Type            : {policy.get_insurance_type_display()}",
        f"Insurer         : {policy.insurer or '—'}",
        f"Plan            : {policy.plan_name or '—'}",
        f"Sum assured     : ₹{inr(policy.sum_insured or 0)}",
        f"Premium         : ₹{inr(policy.premium_amount or 0)}",
        f"Cover started   : {policy.start_date:%d %b %Y}" if policy.start_date else "Cover started   : —",
        f"Renewal due     : {policy.end_date:%d %b %Y}",
        "",
        f"Client          : {client.name}",
        f"Phone           : {client.phone or '—'}",
        f"Nominee         : {policy.nominee_name or '—'}"
        + (f" ({policy.nominee_relationship})" if policy.nominee_relationship else ""),
    ]
    if last_renewal:
        lines.append(
            f"Last collected  : ₹{inr(last_renewal.premium_amount or 0)} "
            f"on {last_renewal.premium_collected_on:%d %b %Y}"
        )
    if policy.notes:
        lines += ["", f"Notes: {policy.notes}"]
    lines += ["", "Call the client and collect the renewal before the policy lapses."]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Renewal reminders (ringing task + push) a month and a week before a policy renews."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        due_dates = [today + timedelta(days=d) for d in REMIND_DAYS]
        policies = (
            InsurancePolicy.objects
            .filter(status=InsurancePolicy.STATUS_ACTIVE, end_date__in=due_dates)
            .select_related("client", "client__mapped_to__user",
                            "relationship_manager__user")
        )

        tasks_made, rung = 0, 0
        for policy in policies:
            # The employee who owns the client relationship makes the renewal
            # call; fall back to the RM the policy was booked under.
            emp = policy.client.mapped_to or policy.relationship_manager
            if not emp or not emp.user_id:
                continue

            due = policy.end_date
            days = (due - today).days
            key = f"polrenew:{policy.id}:{due.isoformat()}"
            task = Task.objects.filter(assign_group=key, is_deleted=False).first()
            if task is None:
                last = policy.renewals.order_by("-premium_collected_on").first()
                task = Task.objects.create(
                    title=(f"Renewal Reminder · {policy.client.name} · "
                           f"{policy.get_insurance_type_display()} "
                           f"{policy.policy_number or '(no number)'}")[:255],
                    description=_describe(policy, last),
                    assigned_to=emp, client=policy.client,
                    due_date=due, due_time=DUE_TIME,
                    priority=Task.PRIORITY_HIGH, assign_group=key,
                )
                tasks_made += 1

            if task.status != Task.STATUS_COMPLETED:
                ring_task(
                    emp.user,
                    f"Renewal in {days} days — call {policy.client.name}",
                    f"{policy.get_insurance_type_display()} {policy.policy_number} "
                    f"renews {due:%d %b}. ₹{inr(policy.premium_amount or 0)} to collect.",
                    task,
                )
                rung += 1

        self.stdout.write(self.style.SUCCESS(
            f"Renewal reminders: {tasks_made} task(s) created, {rung} alert(s) rung."
        ))

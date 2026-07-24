"""Monthly EMI-collection reminders for multiyear health policies sold on EMI.

Runs on the 3rd–5th of each month. For every EMI policy whose EMI window covers
the current month, it pushes the client's mapped employee and — once per month —
auto-assigns a "call client for EMI" task due the 5th, so the call happens before
the due date. A missed EMI can cancel the policy, so this is high priority.

EMI window: starts the month AFTER the sale, runs `emi_months` (5/8/11) months.

In CRONJOBS (scheduled 3rd–5th); the day guard makes it a no-op otherwise.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Sale, Task
from clients.services.tasks import create_notification


def emi_window_contains(sale, year, month):
    """True if (year, month) is within the EMI schedule — the `emi_months` months
    that start the month AFTER the sale."""
    start_month = sale.date.month + 1
    start_year = sale.date.year
    if start_month > 12:
        start_month = 1
        start_year += 1
    offset = (year - start_year) * 12 + (month - start_month)
    return 0 <= offset < (sale.emi_months or 0)


class Command(BaseCommand):
    help = "EMI-collection reminders (push + auto task) for multiyear health policies on EMI."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        if today.day not in (3, 4, 5):
            return

        due = today.replace(day=5)
        ym = f"{today.year}-{today.month:02d}"
        pushes, tasks_made = 0, 0

        sales = (
            Sale.objects.filter(emi_months__gt=0, status=Sale.STATUS_APPROVED)
            .select_related("client", "client__mapped_to__user", "employee__user")
        )
        for sale in sales:
            if not sale.client_id or not emi_window_contains(sale, today.year, today.month):
                continue
            # The employee who owns the client relationship chases the EMI; fall
            # back to the selling employee when the client isn't mapped.
            emp = sale.client.mapped_to or sale.employee
            if not emp or not emp.user_id:
                continue

            key = f"emi:{sale.id}:{ym}"  # one task per policy per month
            task = Task.objects.filter(assign_group=key, is_deleted=False).first()
            if task is None:
                task = Task.objects.create(
                    title=f"Call {sale.client.name} — EMI due 5th",
                    description=(
                        f"Monthly EMI for multiyear policy {sale.policy_number or '(no number)'}"
                        f" — ₹{sale.annual_premium} this year. Call the client to ensure payment"
                        f" before the 5th; a missed EMI can cancel the policy."
                    ),
                    assigned_to=emp, client=sale.client,
                    due_date=due, priority=Task.PRIORITY_HIGH, assign_group=key,
                )
                tasks_made += 1

            # Push on each of the 3rd–5th until the call is logged done.
            if task.status != Task.STATUS_COMPLETED:
                create_notification(
                    emp.user,
                    "EMI due 5th — call client",
                    f"{sale.client.name}: EMI payment due the 5th. Please call before the due date.",
                    link=f"/clients/tasks/{task.id}/",
                )
                pushes += 1

        self.stdout.write(self.style.SUCCESS(
            f"EMI reminders: {tasks_made} task(s) created, {pushes} push(es) sent."
        ))

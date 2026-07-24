"""Renewal reminders for insurance policies that must be renewed manually.

Runs daily. For each approved Health/Life policy it finds the next renewal date
(a multiyear policy is paid through its term, so nothing fires until the term
ends) and, at 30 / 15 / 5 days before it, pushes the client's mapped employee and
auto-assigns a "call client to renew" task due on the renewal date. The task
shows on the home calendar; the push and the insurance_renewal calendar marker
cover the rest — reminders everywhere, as intended.

In CRONJOBS (daily). One task per policy per renewal cycle, deduped via
Task.assign_group = "renewal:<sale>:<renewal-date>".
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from clients.models import Sale, Task
from clients.services.tasks import create_notification

REMIND_DAYS = (30, 15, 5)


class Command(BaseCommand):
    help = "Renewal reminders (push + task) at 30/15/5 days before an insurance policy renews."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        qs = (
            Sale.objects.filter(status=Sale.STATUS_APPROVED)
            .select_related("client", "client__mapped_to__user", "employee__user", "product_ref")
            .filter(
                Q(product_ref__code__in=["HEALTH_INS", "LIFE_INS"])
                | Q(product__iexact="Health Insurance")
                | Q(product__iexact="Life Insurance")
            )
        )
        tasks_made, pushes = 0, 0
        for sale in qs:
            nxt = sale.next_renewal_date(today)
            if not nxt:
                continue
            days = (nxt - today).days
            if days not in REMIND_DAYS:
                continue
            # The employee who owns the client relationship makes the renewal
            # call; fall back to the seller when the client isn't mapped.
            emp = (sale.client.mapped_to if sale.client_id else None) or sale.employee
            if not emp or not emp.user_id:
                continue

            label = sale.product_ref.name if sale.product_ref_id else sale.product
            key = f"renewal:{sale.id}:{nxt.isoformat()}"
            task = Task.objects.filter(assign_group=key, is_deleted=False).first()
            if task is None:
                task = Task.objects.create(
                    title=f"Renew {label} — {sale.client.name} (due {nxt:%d %b})",
                    description=(
                        f"Policy {sale.policy_number or '(no number)'} renews on {nxt:%d %b %Y}. "
                        f"Call the client to complete the renewal before it lapses."
                    ),
                    assigned_to=emp, client=sale.client,
                    due_date=nxt, priority=Task.PRIORITY_HIGH, assign_group=key,
                )
                tasks_made += 1

            if task.status != Task.STATUS_COMPLETED:
                create_notification(
                    emp.user,
                    f"Renewal in {days} days — call client",
                    f"{sale.client.name}: {label} renews {nxt:%d %b}. Call to complete the renewal.",
                    link=f"/clients/tasks/{task.id}/",
                )
                pushes += 1

        self.stdout.write(self.style.SUCCESS(
            f"Renewal reminders: {tasks_made} task(s) created, {pushes} push(es) sent."
        ))

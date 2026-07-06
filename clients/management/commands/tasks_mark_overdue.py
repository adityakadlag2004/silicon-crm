"""Flip live tasks whose deadline has passed to Overdue + notify.

Runs every 15 minutes via CRONJOBS. A task is overdue when its due date/time
(end-of-day when no time is set) is in the past and it is still Pending or
In Progress. Creating the Notification auto-mirrors a push to the assignee's
phone via signals.push_on_notification.
"""
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from clients.models import Notification, Task


class Command(BaseCommand):
    help = "Mark past-due Pending/In Progress tasks as Overdue and notify watchers."

    def handle(self, *args, **options):
        now = timezone.now()
        candidates = (
            Task.objects.filter(
                is_deleted=False,
                status__in=[Task.STATUS_PENDING, Task.STATUS_IN_PROGRESS],
                due_date__isnull=False,
            )
            .select_related("assigned_to__user")
        )

        flipped = 0
        for task in candidates:
            due = task.due_at
            if not due or due >= now:
                continue
            task.status = Task.STATUS_OVERDUE
            task.save(update_fields=["status", "updated_at"])
            flipped += 1

            link = reverse("clients:task_detail", args=[task.pk])
            # Notify assignee + subscribers.
            recipients = {}
            if task.assigned_to and task.assigned_to.user_id:
                recipients[task.assigned_to.user_id] = task.assigned_to.user
            for sub in task.subscribers.select_related("user"):
                if sub.user_id:
                    recipients[sub.user_id] = sub.user
            for user in recipients.values():
                Notification.objects.create(
                    recipient=user,
                    title="Task overdue",
                    body=f"“{task.title}” has passed its due date.",
                    link=link,
                )

        if flipped:
            self.stdout.write(self.style.SUCCESS(f"Marked {flipped} task(s) overdue."))

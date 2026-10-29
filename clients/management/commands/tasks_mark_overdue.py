"""Flip live tasks whose deadline has passed to Overdue + notify, and
escalate long-overdue work to whoever delegated it.

Runs every 15 minutes via CRONJOBS. A task is overdue when its due date/time
(end-of-day when no time is set) is in the past and it is still Pending or
In Progress. Creating the Notification auto-mirrors a push to the assignee's
phone via signals.push_on_notification.

Escalation: tasks overdue ESCALATE_AFTER_DAYS+ ring their creator (and every
admin) once a day as a per-person digest — "3 tasks overdue 2+ days: Ravi ×2,
Priya ×1" — so stuck work can't stay invisible to management. The daily gate
is the existence of today's escalation Notification row (no extra state).
"""
from collections import Counter, defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Employee, Notification, Task
from clients.services.push import send_data_push_to_user
from clients.services.tasks import notify_from_cron

ESCALATE_AFTER_DAYS = 2
ESCALATION_TITLE = "🚨 Overdue tasks need attention"
ESCALATION_HOUR = 9  # first cron run at/after 09:00 sends the day's digest


class Command(BaseCommand):
    help = "Mark past-due Pending/In Progress tasks as Overdue and notify watchers."

    def _escalate(self, now):
        """Daily ringing digest of ≥2-day-overdue tasks to creators + admins."""
        local = timezone.localtime(now)
        if local.hour < ESCALATION_HOUR:
            return 0
        # Overdue rows AND in-progress tasks whose deadline blew past 2+ days
        # ago — "I started it" must not exempt work from escalation.
        stale = (
            Task.objects.filter(
                is_deleted=False,
                status__in=[Task.STATUS_OVERDUE, Task.STATUS_IN_PROGRESS],
                due_date__lte=local.date() - timedelta(days=ESCALATE_AFTER_DAYS),
            ).select_related("assigned_to__user", "created_by")
        )
        if not stale:
            return 0

        # Who hears about what: each creator their own tasks; admins everything.
        per_user = defaultdict(list)
        for task in stale:
            if task.created_by_id:
                per_user[task.created_by].append(task)
        for emp in Employee.objects.filter(active=True, role="admin").select_related("user"):
            if emp.user_id:
                per_user[emp.user] = list(stale)

        sent = 0
        for user, tasks in per_user.items():
            # Follow-ups and reminders are created by the inactive system
            # user; a digest written to it is read by nobody.
            if not user.is_active:
                continue
            already = Notification.objects.filter(
                recipient=user, title=ESCALATION_TITLE, created_at__date=local.date()
            ).exists()
            if already:
                continue
            names = Counter(
                (t.assigned_to.user.get_full_name() or t.assigned_to.user.username)
                if t.assigned_to and t.assigned_to.user_id else "Unassigned"
                for t in tasks
            )
            body = (
                f"{len(tasks)} task(s) overdue {ESCALATE_AFTER_DAYS}+ days: "
                + ", ".join(f"{n} ×{c}" for n, c in names.most_common())
            )
            notification = Notification(
                recipient=user, title=ESCALATION_TITLE, body=body, link="/clients/tasks/all/",
            )
            notification._skip_push = True
            notification.save()
            send_data_push_to_user(user, {
                "kind": "task_alarm",
                "title": ESCALATION_TITLE,
                "body": body,
                "link": "/clients/tasks/all/",
            })
            sent += 1
        return sent

    def handle(self, *args, **options):
        now = timezone.now()
        # Only PENDING flips to Overdue. In Progress means someone is actively
        # on it — flipping it back made "start working on an overdue task"
        # impossible (the cron kept reverting the status within 15 minutes).
        # Late in-progress tasks stay visible via the `late` flag and the
        # escalation digest below.
        # Nothing due after today can be late yet — leave those rows in the
        # database instead of walking every future task every 15 minutes.
        candidates = (
            Task.objects.filter(
                is_deleted=False,
                status=Task.STATUS_PENDING,
                due_date__lte=timezone.localdate(now),
            )
            .select_related("assigned_to__user")
        )

        flipped = 0
        seen = set()   # a watcher of a group task hears once, not once per assignee
        for task in candidates:
            due = task.due_at
            if not due or due >= now:
                continue
            task.status = Task.STATUS_OVERDUE
            task.save(update_fields=["status", "updated_at"])
            flipped += 1
            # event="overdue": the "Task overdue" switch on the settings page
            # was never read, so turning it off changed nothing.
            notify_from_cron(task, "Task overdue", f"“{task.title}” has passed its due date.",
                             event="overdue", seen=seen)

        escalated = self._escalate(now)

        if flipped or escalated:
            self.stdout.write(self.style.SUCCESS(
                f"Marked {flipped} task(s) overdue; escalated to {escalated} inbox(es)."
            ))

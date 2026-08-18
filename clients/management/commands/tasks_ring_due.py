"""Ring task deadlines and unacknowledged assignments on the phone.

Runs every minute via CRONJOBS. Two jobs:

1. **Due-time ring** — a task due at 15:00 rings at 15:00 like an alarm
   (data-only ``task_alarm`` push; the app also arms a local AlarmManager
   alarm from ``due_at_ms`` and the two dedupe by task id on the device).
   ``due_alarm_sent_at`` marks dispatch; editing the due date clears it.

2. **Acknowledgement re-ring** — high/critical tasks the assignee hasn't
   acknowledged keep re-ringing every 4 hours (within 08:00–21:00 so nobody
   is woken at night) until they tap Acknowledge or touch the status.
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from clients.models import Notification, Task
from clients.services.push import send_data_push_to_user

RING_WINDOW = timedelta(minutes=15)   # don't resurrect long-past deadlines
ACK_RERING_EVERY = timedelta(hours=4)
ACK_QUIET_START, ACK_QUIET_END = 21, 8  # no re-rings 21:00–08:00

ACTIVE = [Task.STATUS_PENDING, Task.STATUS_IN_PROGRESS, Task.STATUS_OVERDUE]


def _ring(user, title, body, task, kind="task_alarm"):
    """One ringing push + a silent in-app Notification row.

    A task marked ``silent`` ("don't ring") gets the ordinary Notification
    instead — signals.push_on_notification mirrors it as a plain tray push, so
    the assignee still hears about the deadline without an alarm going off.
    """
    link = f"/clients/tasks/{task.pk}/"
    if task.silent:
        Notification.objects.create(recipient=user, title=title, body=body, link=link)
        return
    notification = Notification(recipient=user, title=title, body=body, link=link)
    notification._skip_push = True  # the ringing push below replaces the mirror
    notification.save()
    send_data_push_to_user(user, {
        "kind": kind,
        "task_id": task.pk,
        "title": title,
        "body": body,
        "link": link,
    })


class Command(BaseCommand):
    help = "Ring due-now tasks and re-ring unacknowledged high/critical tasks."

    def handle(self, *args, **options):
        now = timezone.localtime()
        rung = 0

        # ── 1) Tasks whose exact due moment just arrived ──
        candidates = Task.objects.filter(
            is_deleted=False, status__in=ACTIVE,
            due_date=now.date(), due_time__isnull=False,
            due_alarm_sent_at__isnull=True,
            assigned_to__user__isnull=False,
        ).select_related("assigned_to__user", "client")
        for task in candidates:
            due_at = timezone.make_aware(datetime.combine(task.due_date, task.due_time))
            if not (due_at <= now <= due_at + RING_WINDOW):
                continue
            body = task.title + (f" · {task.client.name}" if task.client_id else "")
            _ring(task.assigned_to.user, "⏰ Task due now", body, task)
            task.due_alarm_sent_at = now
            task.save(update_fields=["due_alarm_sent_at"])
            rung += 1

        # ── 2) Unacknowledged high/critical tasks: re-ring every 4h, daytime ──
        if not (ACK_QUIET_START <= now.hour or now.hour < ACK_QUIET_END):
            cutoff = now - ACK_RERING_EVERY
            unacked = Task.objects.filter(
                is_deleted=False, status__in=ACTIVE,
                priority__in=[Task.PRIORITY_HIGH, Task.PRIORITY_CRITICAL],
                acknowledged_at__isnull=True,
                assigned_to__user__isnull=False,
                created_at__lte=cutoff,
            ).filter(
                Q(ack_last_rung_at__isnull=True) | Q(ack_last_rung_at__lte=cutoff)
            ).select_related("assigned_to__user")
            for task in unacked:
                _ring(
                    task.assigned_to.user,
                    "❗ Task awaiting your acknowledgement",
                    f"“{task.title}” ({task.get_priority_display()}) — open it and tap Acknowledge.",
                    task,
                )
                task.ack_last_rung_at = now
                task.save(update_fields=["ack_last_rung_at"])
                rung += 1

        if rung:
            self.stdout.write(self.style.SUCCESS(f"Rang {rung} task alert(s)."))

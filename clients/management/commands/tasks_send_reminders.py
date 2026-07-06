"""Send day-before / same-day reminders for upcoming tasks.

Runs hourly via CRONJOBS but only acts during the admin-configured hour
(TaskReminderSetting.same_day_hour), so reminders land at a predictable time.
Each task is reminded at most once per window via the reminded_* flags.
Overdue reminders are handled separately by tasks_mark_overdue.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Task, TaskReminderSetting
from clients.services.tasks import notify_task


class Command(BaseCommand):
    help = "Send day-before and same-day task reminders."

    def handle(self, *args, **options):
        setting = TaskReminderSetting.current()
        now = timezone.localtime()
        if now.hour != setting.same_day_hour:
            return

        today = now.date()
        tomorrow = today + timedelta(days=1)
        open_statuses = [Task.STATUS_PENDING, Task.STATUS_IN_PROGRESS]
        sent = 0

        if setting.remind_day_before:
            for task in Task.objects.filter(
                is_deleted=False, status__in=open_statuses,
                due_date=tomorrow, reminded_day_before=False,
            ).select_related("assigned_to__user"):
                notify_task(task, None, "Task due tomorrow",
                            f"“{task.title}” is due tomorrow.")
                task.reminded_day_before = True
                task.save(update_fields=["reminded_day_before"])
                sent += 1

        if setting.remind_same_day:
            for task in Task.objects.filter(
                is_deleted=False, status__in=open_statuses,
                due_date=today, reminded_same_day=False,
            ).select_related("assigned_to__user"):
                notify_task(task, None, "Task due today",
                            f"“{task.title}” is due today.")
                task.reminded_same_day = True
                task.save(update_fields=["reminded_same_day"])
                sent += 1

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder(s)."))

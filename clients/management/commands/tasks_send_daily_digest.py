"""Send each employee a morning WhatsApp summary of their open tasks.

Mirrors the old tasks.automatebusiness.com daily digest:
    ⛔️ Overdue - N   🔴 Pending - N   🟡 In Progress - N   🔜 This Week - N

Runs hourly via CRONJOBS but only acts during the admin-configured hour
(TaskReminderSetting.digest_hour), so the digest lands at a predictable time.
Employees with no phone or no open tasks are skipped. No-op overall when
WhatsApp isn't configured (send_template records the intent as "skipped").
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Employee, Task, TaskReminderSetting
from clients.services.whatsapp import send_template, TEMPLATE_TASK_DIGEST


def digest_counts(employee, today=None):
    """Return (overdue, pending, in_progress, this_week) for an employee's
    open tasks. `this_week` counts still-open tasks due within the next 7 days."""
    today = today or timezone.localdate()
    week_end = today + timedelta(days=7)
    qs = Task.objects.filter(is_deleted=False, assigned_to=employee)

    overdue = qs.filter(status=Task.STATUS_OVERDUE).count()
    pending = qs.filter(status=Task.STATUS_PENDING).count()
    in_progress = qs.filter(status=Task.STATUS_IN_PROGRESS).count()
    this_week = qs.filter(
        status__in=[Task.STATUS_PENDING, Task.STATUS_IN_PROGRESS],
        due_date__gte=today, due_date__lte=week_end,
    ).count()
    return overdue, pending, in_progress, this_week


class Command(BaseCommand):
    help = "Send employees their daily WhatsApp task digest (gated to the configured hour)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Ignore the configured hour and send now (for testing).",
        )

    def handle(self, *args, **options):
        setting = TaskReminderSetting.current()
        if not setting.send_daily_digest:
            return

        now = timezone.localtime()
        if not options["force"] and now.hour != setting.digest_hour:
            return

        today = now.date()
        sent = 0
        for emp in Employee.objects.filter(active=True).select_related("user"):
            if not emp.phone:
                continue
            overdue, pending, in_progress, this_week = digest_counts(emp, today)
            if overdue + pending + in_progress + this_week == 0:
                continue  # nothing open — don't ping (and don't spend a message)

            variables = [
                emp.user.get_full_name() or emp.user.username,  # {{1}}
                str(overdue),                                    # {{2}}
                str(pending),                                    # {{3}}
                str(in_progress),                                # {{4}}
                str(this_week),                                  # {{5}}
            ]
            if send_template(emp.phone, TEMPLATE_TASK_DIGEST, variables):
                sent += 1

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} daily digest(s)."))

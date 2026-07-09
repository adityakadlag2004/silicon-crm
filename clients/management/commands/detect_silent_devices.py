"""Flag employees whose call tracking has gone silent — a likely sign the app
was uninstalled (or call tracking was turned off), since we have no uninstall
signal from the device.

An employee is "silent" when they have synced calls before (so an office SIM
was configured and the app was in use) but no call has been recorded for
``--days`` days (default 3). Each silent streak alerts the admins once: a
repeat run won't re-notify until the employee resumes calling and goes quiet
again.

Wired into CRONJOBS (daily). Run manually:
    venv_new/bin/python manage.py detect_silent_devices --days 3
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from clients.models import CallLogEntry, Employee, Notification
from clients.services.tasks import create_notification


class Command(BaseCommand):
    help = "Alert admins about employees with no synced calls for N days (likely app uninstall)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=3,
            help="Silence threshold in days before alerting (default 3).",
        )

    def handle(self, *args, **options):
        days = max(1, options["days"])
        now = timezone.now()
        cutoff = now - timedelta(days=days)

        # Employees who have tracked calls before, with their most recent call.
        last_call_by_emp = {
            r["employee_id"]: r["last"]
            for r in CallLogEntry.objects.values("employee_id").annotate(last=Max("started_at"))
            if r["last"] and r["last"] < cutoff
        }
        if not last_call_by_emp:
            self.stdout.write("No silent devices.")
            return

        employees = Employee.objects.filter(
            id__in=last_call_by_emp.keys(), active=True
        ).select_related("user")

        admin_users = set(User.objects.filter(is_superuser=True))
        admin_users.update(User.objects.filter(employee__role="admin", employee__active=True))
        if not admin_users:
            self.stdout.write("No admins to notify.")
            return

        flagged = 0
        for emp in employees:
            last_call = last_call_by_emp[emp.id]
            link = reverse("clients:call_analytics") + f"?silent={emp.id}"

            # One alert per silent streak: skip if an alert already exists that
            # was raised after this employee's last recorded call.
            if Notification.objects.filter(link=link, created_at__gte=last_call).exists():
                continue

            name = emp.user.get_full_name() or emp.user.username
            gap_days = (now - last_call).days
            title = "⚠️ No call data — possible uninstall"
            body = (
                f"{name} hasn't synced any calls for {gap_days} days "
                f"(last on {timezone.localtime(last_call).strftime('%d %b')}). "
                f"They may have uninstalled the app or turned off call tracking."
            )
            for admin_user in admin_users:
                create_notification(admin_user, title, body, link)
            flagged += 1

        self.stdout.write(f"Flagged {flagged} silent device(s) for {len(admin_users)} admin(s).")

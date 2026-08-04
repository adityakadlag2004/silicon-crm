"""Tests for the cleanup_data retention command (weekly cron).

Run: .venv/bin/python manage.py test clients.test.test_cleanup_data -v 2
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from clients.models import AuditLog, CallFollowUp, CallLogEntry, Employee, Notification


class CleanupDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="cd_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)

    def _run(self, *args):
        out = StringIO()
        call_command("cleanup_data", *args, stdout=out)
        return out.getvalue()

    def _make_call(self, days_ago, phone="9000000001"):
        return CallLogEntry.objects.create(
            employee=self.emp,
            phone=phone,
            direction=CallLogEntry.DIRECTION_OUTGOING,
            connected=True,
            duration_seconds=30,
            started_at=timezone.now() - timedelta(days=days_ago),
        )

    def _make_followup(self, days_ago, status):
        fu = CallFollowUp.objects.create(
            employee=self.emp,
            phone="9000000002",
            scheduled_at=timezone.now() - timedelta(days=days_ago),
            status=status,
        )
        # created_at is auto_now_add; backdate it directly.
        CallFollowUp.objects.filter(pk=fu.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        return fu

    def test_old_call_logs_deleted_recent_kept(self):
        old = self._make_call(days_ago=400, phone="9000000010")
        recent = self._make_call(days_ago=10, phone="9000000011")

        self._run()

        self.assertFalse(CallLogEntry.objects.filter(pk=old.pk).exists())
        self.assertTrue(CallLogEntry.objects.filter(pk=recent.pk).exists())

    def test_call_days_option_overrides_window(self):
        borderline = self._make_call(days_ago=100, phone="9000000012")

        self._run("--call-days", "60")

        self.assertFalse(CallLogEntry.objects.filter(pk=borderline.pk).exists())

    def test_finished_followups_deleted_pending_kept(self):
        old_done = self._make_followup(days_ago=120, status=CallFollowUp.STATUS_DONE)
        old_dismissed = self._make_followup(days_ago=120, status=CallFollowUp.STATUS_DISMISSED)
        old_pending = self._make_followup(days_ago=120, status=CallFollowUp.STATUS_PENDING)
        recent_done = self._make_followup(days_ago=5, status=CallFollowUp.STATUS_DONE)

        self._run()

        self.assertFalse(CallFollowUp.objects.filter(pk=old_done.pk).exists())
        self.assertFalse(CallFollowUp.objects.filter(pk=old_dismissed.pk).exists())
        self.assertTrue(CallFollowUp.objects.filter(pk=old_pending.pk).exists())
        self.assertTrue(CallFollowUp.objects.filter(pk=recent_done.pk).exists())

    def test_old_read_notifications_deleted(self):
        notif = Notification.objects.create(
            recipient=self.user, title="t", body="b", is_read=True
        )
        Notification.objects.filter(pk=notif.pk).update(
            created_at=timezone.now() - timedelta(days=120)
        )

        self._run()

        self.assertFalse(Notification.objects.filter(pk=notif.pk).exists())

    def test_old_audit_logs_deleted_recent_kept(self):
        old = AuditLog.objects.create(
            action="app.crash", summary="old",
            created_at=timezone.now() - timedelta(days=400),
        )
        recent = AuditLog.objects.create(
            action="sale.approved", summary="recent",
            created_at=timezone.now() - timedelta(days=30),
        )

        self._run()

        self.assertFalse(AuditLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=recent.pk).exists())

    def test_dry_run_deletes_nothing(self):
        old_call = self._make_call(days_ago=400, phone="9000000013")
        old_done = self._make_followup(days_ago=120, status=CallFollowUp.STATUS_DONE)

        out = self._run("--dry-run")

        self.assertTrue(CallLogEntry.objects.filter(pk=old_call.pk).exists())
        self.assertTrue(CallFollowUp.objects.filter(pk=old_done.pk).exists())
        self.assertIn("Dry run", out)

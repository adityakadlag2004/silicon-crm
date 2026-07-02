"""Tests for call tracking: sync API, follow-ups, reminders, admin analytics.

Run: venv_new/bin/python manage.py test clients.test.test_call_tracking -v 2
"""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    CallFollowUp,
    CallLogEntry,
    CallTrackingSettings,
    Client,
    Employee,
    Notification,
)


class _CallSetup(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="ct_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="ct_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Callee", phone="9876543210")

    def setUp(self):
        self.admin = TestClient()
        self.admin.force_login(self.admin_user)
        self.employee = TestClient()
        self.employee.force_login(self.emp_user)

    def _sync(self, http, events):
        return http.post(
            reverse("clients:calls_sync"),
            data=json.dumps({"events": events}),
            content_type="application/json",
        )


class CallSyncTests(_CallSetup):
    def test_sync_creates_entry_and_matches_client(self):
        # 12:00 local — inside the default 10:00-18:00 window
        started = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        resp = self._sync(self.employee, [{
            "phone": "+919876543210",
            "direction": "outgoing",
            "connected": True,
            "duration_seconds": 95,
            "started_at": int(started.timestamp() * 1000),
        }])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["created"], 1)
        entry = CallLogEntry.objects.get()
        self.assertEqual(entry.employee, self.emp)
        self.assertEqual(entry.client, self.customer)  # matched via last-10-digits
        self.assertTrue(entry.connected)

    def test_sync_is_idempotent(self):
        started = timezone.now()
        ev = {
            "phone": "12345", "direction": "incoming", "connected": False,
            "duration_seconds": 0, "started_at": int(started.timestamp() * 1000),
        }
        self._sync(self.employee, [ev])
        resp = self._sync(self.employee, [ev])
        self.assertEqual(resp.json()["created"], 0)
        self.assertEqual(CallLogEntry.objects.count(), 1)

    def test_sync_rejects_bad_direction(self):
        resp = self._sync(self.employee, [{
            "phone": "123", "direction": "sideways", "started_at": 1710000000000,
        }])
        self.assertEqual(resp.json()["created"], 0)


class FollowUpTests(_CallSetup):
    def test_create_followup_15m(self):
        resp = self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps({"phone": "9876543210", "choice": "15m"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        fu = CallFollowUp.objects.get()
        self.assertEqual(fu.employee, self.emp)
        self.assertEqual(fu.client, self.customer)
        self.assertAlmostEqual(
            (fu.scheduled_at - timezone.now()).total_seconds(), 15 * 60, delta=30
        )

    def test_invalid_choice_rejected(self):
        resp = self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps({"phone": "9876543210", "choice": "someday"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_reminder_command_notifies_once(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", client=self.customer,
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        call_command("send_followup_reminders")
        note = Notification.objects.get(recipient=self.emp_user)
        self.assertTrue(note.link.startswith("tel:"))
        fu.refresh_from_db()
        self.assertTrue(fu.reminded)
        # Second run must not duplicate
        call_command("send_followup_reminders")
        self.assertEqual(Notification.objects.filter(recipient=self.emp_user).count(), 1)

    def test_mark_done(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="123", scheduled_at=timezone.now()
        )
        resp = self.employee.post(
            reverse("clients:call_followup_update", args=[fu.id]), {"action": "done"}
        )
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_DONE)

    def test_cannot_touch_others_followup(self):
        fu = CallFollowUp.objects.create(
            employee=self.admin_emp, phone="123", scheduled_at=timezone.now()
        )
        resp = self.employee.post(
            reverse("clients:call_followup_update", args=[fu.id]), {"action": "done"}
        )
        self.assertEqual(resp.status_code, 403)


class AnalyticsTests(_CallSetup):
    def test_analytics_admin_only(self):
        self.assertEqual(self.employee.get(reverse("clients:call_analytics")).status_code, 403)
        self.assertEqual(self.admin.get(reverse("clients:call_analytics")).status_code, 200)

    def test_after_hours_calls_excluded(self):
        local_now = timezone.localtime()
        inside = local_now.replace(hour=11, minute=0, second=0, microsecond=0)
        outside = local_now.replace(hour=21, minute=0, second=0, microsecond=0)
        CallLogEntry.objects.create(
            employee=self.emp, phone="1", direction="outgoing",
            connected=True, duration_seconds=60, started_at=inside,
        )
        CallLogEntry.objects.create(
            employee=self.emp, phone="2", direction="outgoing",
            connected=True, duration_seconds=60, started_at=outside,
        )
        resp = self.admin.get(reverse("clients:call_analytics"))
        self.assertEqual(resp.context["totals"]["dialed"], 1)

    def test_admin_can_update_work_hours(self):
        self.admin.post(reverse("clients:call_analytics"), {
            "form": "settings", "work_start": "09:30", "work_end": "19:00", "enabled": "on",
        })
        cfg = CallTrackingSettings.current()
        self.assertEqual(cfg.work_start.hour, 9)
        self.assertEqual(cfg.work_end.hour, 19)

    def test_config_endpoint(self):
        resp = self.employee.get(reverse("clients:call_config"))
        data = resp.json()
        self.assertEqual(data["work_start_minutes"], 600)
        self.assertEqual(data["work_end_minutes"], 1080)

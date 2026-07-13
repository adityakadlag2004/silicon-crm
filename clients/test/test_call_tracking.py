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
        # Count all 7 days so "today" assertions are day-of-week independent.
        CallTrackingSettings.objects.update_or_create(
            pk=1, defaults={"work_days": "0,1,2,3,4,5,6"}
        )

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


    def test_new_grid_choices(self):
        # Minute/hour choices: exact offset
        resp = self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps({"phone": "9876543210", "choice": "10m"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        fu = CallFollowUp.objects.latest("id")
        self.assertAlmostEqual(
            (fu.scheduled_at - timezone.now()).total_seconds(), 10 * 60, delta=30
        )
        # Day+ choices: 10:00 on the target day
        resp = self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps({"phone": "9876543210", "choice": "2mo"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        fu = CallFollowUp.objects.latest("id")
        local = timezone.localtime(fu.scheduled_at)
        self.assertEqual(local.hour, 10)
        self.assertEqual((local.date() - timezone.localdate()).days, 60)

    def test_legacy_choices_still_work(self):
        resp = self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps({"phone": "9876543210", "choice": "tomorrow"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

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


class AppCallAnalyticsTests(_CallSetup):
    def test_admin_only(self):
        resp = self.employee.get(reverse("clients:app_call_analytics"))
        self.assertEqual(resp.status_code, 403)

    def test_employee_and_range_filters(self):
        from datetime import timedelta as td
        base = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        other_user = User.objects.create_user(username="ct_emp2", password="x")
        other = Employee.objects.create(user=other_user, role="employee", salary=0, active=True)
        CallLogEntry.objects.create(employee=self.emp, phone="1", direction="outgoing",
                                    connected=True, duration_seconds=60, started_at=base)
        CallLogEntry.objects.create(employee=other, phone="2", direction="outgoing",
                                    connected=True, duration_seconds=30, started_at=base)
        CallLogEntry.objects.create(employee=self.emp, phone="3", direction="outgoing",
                                    connected=True, duration_seconds=30, started_at=base - td(days=3))

        data = self.admin.get(reverse("clients:app_call_analytics"), {"range": "today"}).json()
        self.assertEqual(data["totals"]["dialed"], 2)

        data = self.admin.get(
            reverse("clients:app_call_analytics"),
            {"range": "week", "employee_id": self.emp.id},
        ).json()
        self.assertEqual(data["totals"]["dialed"], 2)  # today's + 3 days ago, own only
        self.assertTrue(all(c["employee"] for c in data["calls"]))

    def test_by_employee_breakdown_covers_whole_team(self):
        base = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        CallLogEntry.objects.create(employee=self.emp, phone="1", direction="outgoing",
                                    connected=True, duration_seconds=200, started_at=base)
        # admin_emp has no calls -> should still appear with zeros
        data = self.admin.get(reverse("clients:app_call_analytics"), {"range": "today"}).json()
        rows = {r["name"]: r for r in data["by_employee"]}
        # both active employees present
        self.assertIn(self.emp.user.username, [r["name"] for r in data["by_employee"]] +
                      [self.emp.user.get_full_name()])
        emp_row = next(r for r in data["by_employee"] if r["calls"] == 1)
        self.assertEqual(emp_row["connected"], 1)
        self.assertEqual(emp_row["serious"], 1)  # 200s > 150s

    def test_app_matches_web_definitions(self):
        """Regression: app "Connected" / per-employee "calls" must use the web's
        outgoing-only definitions. The app previously counted connected calls of
        both directions (and all calls in the team table), so web and app showed
        different numbers for the same day."""
        base = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        mk = CallLogEntry.objects.create
        mk(employee=self.emp, phone="1", direction="outgoing",
           connected=True, duration_seconds=60, started_at=base)
        mk(employee=self.emp, phone="2", direction="outgoing",
           connected=False, duration_seconds=0, started_at=base)
        mk(employee=self.emp, phone="3", direction="incoming",
           connected=True, duration_seconds=120, started_at=base)  # inflated app before
        mk(employee=self.emp, phone="4", direction="incoming",
           connected=False, duration_seconds=0, started_at=base)

        app = self.admin.get(reverse("clients:app_call_analytics"), {"range": "today"}).json()
        web = self.admin.get(reverse("clients:call_analytics")).context["totals"]

        self.assertEqual(app["totals"]["dialed"], 2)
        self.assertEqual(app["totals"]["connected"], 1)  # outgoing+connected only
        self.assertEqual(app["totals"]["connected"], web["dialed_connected"])
        self.assertEqual(app["totals"]["received"], web["received"])
        self.assertEqual(app["totals"]["missed"], web["missed"])
        self.assertEqual(app["totals"]["talk_minutes"], web["talk_minutes"])

        emp_row = next(r for r in app["by_employee"] if r["id"] == self.emp.id)
        self.assertEqual(emp_row["calls"], 2)      # dialed, not all 4 directions
        self.assertEqual(emp_row["connected"], 1)  # outgoing+connected


class FollowupChoicesAndCustomTests(_CallSetup):
    """New popup abilities: server-driven chip grid, semantic choices,
    exact custom date/time, and the note field."""

    def test_config_returns_admin_configured_grid(self):
        from clients.models import CallTrackingSettings
        cfg = CallTrackingSettings.current()
        cfg.popup_choices = "15m,eve,1d,bogus_key"
        cfg.save()
        data = self.employee.get(reverse("clients:call_config")).json()
        chips = data["popup_choices"]
        self.assertEqual([c["key"] for c in chips], ["15m", "eve", "1d"])  # bogus dropped
        self.assertEqual(chips[1]["label"], "Today 6 PM")

    def _create(self, body):
        return self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps(body), content_type="application/json")

    def test_semantic_choices_schedule_correctly(self):
        resp = self._create({"phone": "9876543210", "choice": "tom_am"}).json()
        self.assertTrue(resp["ok"])
        fu = CallFollowUp.objects.get(pk=resp["id"])
        sched = timezone.localtime(fu.scheduled_at)
        self.assertEqual(sched.date(), timezone.localdate() + timedelta(days=1))
        self.assertEqual((sched.hour, sched.minute), (10, 0))

        resp = self._create({"phone": "9876543210", "choice": "mon_am"}).json()
        sched = timezone.localtime(CallFollowUp.objects.get(pk=resp["id"]).scheduled_at)
        self.assertEqual(sched.weekday(), 0)  # Monday
        self.assertGreater(sched, timezone.localtime())

        resp = self._create({"phone": "9876543210", "choice": "eve"}).json()
        sched = timezone.localtime(CallFollowUp.objects.get(pk=resp["id"]).scheduled_at)
        self.assertEqual((sched.hour, sched.minute), (18, 0))
        self.assertGreater(sched, timezone.localtime())

    def test_custom_datetime_and_note(self):
        target = (timezone.localtime() + timedelta(days=3)).replace(
            hour=16, minute=30, second=0, microsecond=0)
        resp = self._create({
            "phone": "9876543210",
            "custom_at": target.strftime("%Y-%m-%dT%H:%M"),
            "note": "discuss SIP top-up",
        }).json()
        self.assertTrue(resp["ok"])
        fu = CallFollowUp.objects.get(pk=resp["id"])
        self.assertEqual(timezone.localtime(fu.scheduled_at), target)
        self.assertEqual(fu.note, "discuss SIP top-up")

    def test_custom_datetime_must_be_future(self):
        past = (timezone.localtime() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        resp = self._create({"phone": "9876543210", "custom_at": past})
        self.assertEqual(resp.status_code, 400)

    def test_legacy_keys_still_accepted(self):
        resp = self._create({"phone": "9876543210", "choice": "tomorrow"})
        self.assertEqual(resp.status_code, 200)


class AppSettingsApiTests(_CallSetup):
    """GET/POST /api/app/settings/ — the native Settings screen backend."""

    def test_admin_only(self):
        self.assertEqual(
            self.employee.get(reverse("clients:app_settings")).status_code, 403)

    def test_get_returns_all_sections(self):
        data = self.admin.get(reverse("clients:app_settings")).json()
        self.assertIn("call_tracking", data)
        self.assertIn("popup", data)
        self.assertIn("tasks", data)
        self.assertEqual(data["call_tracking"]["work_start"], "10:00")
        self.assertIn({"key": "eve", "label": "Today 6 PM"}, data["popup"]["catalog"])

    def _post(self, body):
        return self.admin.post(
            reverse("clients:app_settings"),
            data=json.dumps(body), content_type="application/json")

    def test_update_call_tracking_section(self):
        from clients.models import CallTrackingSettings
        data = self._post({
            "section": "call_tracking", "enabled": False,
            "work_start": "09:30", "work_end": "19:00", "work_days": [0, 1, 2],
        }).json()
        self.assertTrue(data["ok"])
        cfg = CallTrackingSettings.current()
        cfg.refresh_from_db()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.work_start.strftime("%H:%M"), "09:30")
        self.assertEqual(cfg.work_days, "0,1,2")

    def test_update_popup_choices_filters_invalid(self):
        from clients.models import CallTrackingSettings
        self._post({"section": "popup", "popup_choices": ["eve", "nope", "15m"]})
        cfg = CallTrackingSettings.current()
        cfg.refresh_from_db()
        self.assertEqual(cfg.popup_choices, "eve,15m")
        # An all-invalid list must not wipe the grid
        self._post({"section": "popup", "popup_choices": ["zzz"]})
        cfg.refresh_from_db()
        self.assertEqual(cfg.popup_choices, "eve,15m")

    def test_update_tasks_section(self):
        from clients.models import TaskReminderSetting
        self._post({"section": "tasks", "remind_day_before": False, "same_day_hour": 8})
        r = TaskReminderSetting.current()
        r.refresh_from_db()
        self.assertFalse(r.remind_day_before)
        self.assertEqual(r.same_day_hour, 8)

    def test_bad_hhmm_and_section_rejected(self):
        resp = self._post({"section": "call_tracking", "work_start": "25:99"})
        self.assertTrue(resp.json()["ok"])  # bad time ignored, keeps current
        self.assertEqual(resp.json()["call_tracking"]["work_start"], "10:00")
        self.assertEqual(self._post({"section": "nope"}).status_code, 400)


class WorkDayAndPopupSettingsTests(TestCase):
    """Tracking excludes non-work days (e.g. Sunday); popup window is independent."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="wd_admin", password="x")
        Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="wd_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)

    def _admin(self):
        c = TestClient(); c.force_login(self.admin); return c

    def _next_weekday(self, base, target_py_weekday):
        # target_py_weekday: Mon=0..Sun=6
        d = base
        while d.weekday() != target_py_weekday:
            d += timedelta(days=1)
        return d

    def test_sunday_calls_excluded_from_analytics(self):
        cfg = CallTrackingSettings.current()
        cfg.work_days = "0,1,2,3,4,5"  # Mon–Sat, exclude Sunday
        cfg.save()
        base = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        saturday = self._next_weekday(base, 5)   # Sat
        sunday = self._next_weekday(base, 6)      # Sun
        CallLogEntry.objects.create(employee=self.emp, phone="1", direction="outgoing",
                                    connected=True, duration_seconds=60, started_at=saturday)
        CallLogEntry.objects.create(employee=self.emp, phone="2", direction="outgoing",
                                    connected=True, duration_seconds=60, started_at=sunday)
        # analytics "month" range covers both; Sunday one must be excluded
        data = self._admin().get(reverse("clients:app_call_analytics"), {"range": "month"}).json()
        self.assertEqual(data["totals"]["dialed"], 1)

    def test_config_exposes_independent_windows(self):
        cfg = CallTrackingSettings.current()
        cfg.work_days = "0,1,2,3,4,5"
        cfg.popup_days = "0,1,2,3,4,5,6"
        cfg.popup_enabled = True
        cfg.save()
        c = TestClient(); c.force_login(self.emp_user)
        data = c.get(reverse("clients:call_config")).json()
        self.assertEqual(data["work_days"], [0, 1, 2, 3, 4, 5])
        self.assertEqual(data["popup_days"], [0, 1, 2, 3, 4, 5, 6])  # every day
        self.assertTrue(data["popup_enabled"])

    def test_admin_form_saves_days_and_popup(self):
        self._admin().post(reverse("clients:call_analytics"), {
            "form": "settings",
            "work_start": "10:00", "work_end": "18:00",
            "popup_start": "08:00", "popup_end": "22:00",
            "enabled": "on", "popup_enabled": "on",
            "work_days": ["0", "1", "2", "3", "4", "5"],   # no Sunday
            "popup_days": ["0", "1", "2", "3", "4", "5", "6"],
        })
        cfg = CallTrackingSettings.current()
        self.assertEqual(cfg.work_days, "0,1,2,3,4,5")
        self.assertEqual(cfg.popup_days, "0,1,2,3,4,5,6")
        self.assertEqual(cfg.popup_start.hour, 8)
        self.assertEqual(cfg.popup_end.hour, 22)


class SilentDeviceAlertTests(_CallSetup):
    """`detect_silent_devices`: admins are alerted when a previously-active
    caller stops syncing calls (a proxy for app uninstall)."""

    def _run(self, days=3):
        call_command("detect_silent_devices", "--days", str(days))

    def _alerts(self):
        return Notification.objects.filter(
            recipient=self.admin_user, link__contains=f"silent={self.emp.id}"
        )

    def test_silent_caller_alerts_admin_once(self):
        CallLogEntry.objects.create(
            employee=self.emp, phone="1", direction="outgoing", connected=True,
            duration_seconds=30, started_at=timezone.now() - timedelta(days=5),
        )
        self._run()
        self.assertEqual(self._alerts().count(), 1)
        # Same silent streak → no duplicate on a second run.
        self._run()
        self.assertEqual(self._alerts().count(), 1)

    def test_recent_caller_not_flagged(self):
        CallLogEntry.objects.create(
            employee=self.emp, phone="1", direction="outgoing", connected=True,
            duration_seconds=30, started_at=timezone.now() - timedelta(days=1),
        )
        self._run()
        self.assertFalse(self._alerts().exists())

    def test_never_called_not_flagged(self):
        # No CallLogEntry ever → never tracked, so not an uninstall signal.
        self._run()
        self.assertFalse(self._alerts().exists())

    def test_resumed_then_silent_again_realerts(self):
        CallLogEntry.objects.create(
            employee=self.emp, phone="1", direction="outgoing", connected=True,
            duration_seconds=30, started_at=timezone.now() - timedelta(days=6),
        )
        self._run()
        self.assertEqual(self._alerts().count(), 1)
        # Backdate the first alert to before a newer (still-silent) call, so the
        # employee resumed calling and then went quiet again → a fresh streak.
        self._alerts().update(created_at=timezone.now() - timedelta(days=5))
        CallLogEntry.objects.create(
            employee=self.emp, phone="2", direction="outgoing", connected=True,
            duration_seconds=30, started_at=timezone.now() - timedelta(days=4),
        )
        self._run()
        self.assertEqual(self._alerts().count(), 2)

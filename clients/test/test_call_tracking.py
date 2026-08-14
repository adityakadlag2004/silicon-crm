"""Tests for call tracking: sync API, follow-ups, reminders, admin analytics.

Run: venv_new/bin/python manage.py test clients.test.test_call_tracking -v 2
"""
import json
from datetime import timedelta
from unittest import mock

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
        # The app arms an exact on-device alarm from these fields.
        data = resp.json()
        self.assertEqual(data["id"], fu.id)
        self.assertEqual(data["scheduled_at_ms"], int(fu.scheduled_at.timestamp() * 1000))
        self.assertEqual(data["client"], self.customer.name)


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

    def test_new_followup_supersedes_older_pending_for_same_number(self):
        """One number = one reminder: creating a follow-up retires older
        PENDING ones for the same employee + number (any +91/0 format),
        returns their ids so the app drops their alarms, and leaves other
        numbers and completed history untouched."""
        def create(phone):
            return self.employee.post(
                reverse("clients:call_followup_create"),
                data=json.dumps({"phone": phone, "choice": "15m"}),
                content_type="application/json",
            ).json()

        first = create("9876543210")
        other = create("9000000001")  # different number — must survive
        done = CallFollowUp.objects.create(  # completed history — must survive
            employee=self.emp, phone="9876543210",
            scheduled_at=timezone.now(), status=CallFollowUp.STATUS_DONE,
        )

        second = create("+91 98765 43210")  # same number, different format
        self.assertEqual(second["superseded_ids"], [first["id"]])
        # Kept, not deleted — the chase history is the prioritisation signal.
        self.assertEqual(
            CallFollowUp.objects.get(pk=first["id"]).status,
            CallFollowUp.STATUS_SUPERSEDED,
        )
        self.assertEqual(CallFollowUp.objects.get(pk=second["id"]).attempts, 2)
        self.assertTrue(CallFollowUp.objects.filter(pk=other["id"]).exists())
        self.assertTrue(CallFollowUp.objects.filter(pk=done.pk).exists())
        # Exactly one pending reminder remains for this number.
        pending = [
            f for f in CallFollowUp.objects.filter(
                employee=self.emp, status=CallFollowUp.STATUS_PENDING
            ) if f.phone.replace(" ", "").endswith("9876543210")
        ]
        self.assertEqual([f.pk for f in pending], [second["id"]])

    def test_popup_window_defaults_to_24x7(self):
        """Owner decision: the post-call popup is available 24×7 by default
        (migration 0085 also forces the live row to this)."""
        cfg = CallTrackingSettings()
        self.assertTrue(cfg.popup_enabled)
        self.assertEqual((cfg.popup_start.hour, cfg.popup_start.minute), (0, 0))
        self.assertEqual((cfg.popup_end.hour, cfg.popup_end.minute), (23, 59))
        self.assertEqual(cfg.popup_days, "0,1,2,3,4,5,6")

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

    def test_reminder_command_sends_alarm_push_not_plain_mirror(self):
        """Due follow-ups ring via one data-only alarm push; the generic
        Notification→FCM mirror must stay silent to avoid a duplicate."""
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", client=self.customer,
            scheduled_at=timezone.now() - timedelta(minutes=1), note="discuss SIP",
        )
        with mock.patch(
            "clients.management.commands.send_followup_reminders.send_data_push_to_user"
        ) as data_push, mock.patch("clients.services.push.send_push_to_user") as plain_push:
            call_command("send_followup_reminders")

        data_push.assert_called_once()
        user, payload = data_push.call_args.args
        self.assertEqual(user, self.emp_user)
        self.assertEqual(payload["kind"], "followup_alarm")
        self.assertEqual(payload["followup_id"], fu.id)
        self.assertEqual(payload["client"], self.customer.name)
        self.assertEqual(payload["note"], "discuss SIP")
        self.assertEqual(payload["phone"], "9876543210")
        plain_push.assert_not_called()
        # The in-app Notification row still exists for the Notifications screen.
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

    def test_reschedule_to_a_picked_moment(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="123", scheduled_at=timezone.now(), reminded=True
        )
        target = (timezone.localtime() + timedelta(days=3)).replace(
            hour=15, minute=30, second=0, microsecond=0
        )
        resp = self.employee.post(
            reverse("clients:app_followup_action", args=[fu.id]),
            data=json.dumps({"action": "reschedule", "at": target.strftime("%Y-%m-%dT%H:%M")}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        fu.refresh_from_db()
        self.assertEqual(timezone.localtime(fu.scheduled_at), target)
        self.assertFalse(fu.reminded)  # reminder re-arms

    def test_reschedule_rejects_past(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="123", scheduled_at=timezone.now()
        )
        before = fu.scheduled_at
        resp = self.employee.post(
            reverse("clients:app_followup_action", args=[fu.id]),
            data=json.dumps({"action": "reschedule", "at": "2020-01-01T10:00"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        fu.refresh_from_db()
        self.assertEqual(fu.scheduled_at, before)

    def test_manual_followup_needs_no_call(self):
        """The + button: a number and a moment, no CallLogEntry involved."""
        target = (timezone.localtime() + timedelta(days=1)).replace(
            hour=11, minute=0, second=0, microsecond=0
        )
        resp = self.employee.post(
            reverse("clients:call_followup_create"),
            data=json.dumps({
                "phone": "9876543210", "note": "cold call back",
                "custom_at": target.strftime("%Y-%m-%dT%H:%M"),
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        fu = CallFollowUp.objects.get()
        self.assertEqual(timezone.localtime(fu.scheduled_at), target)
        self.assertEqual(fu.note, "cold call back")

    def test_cannot_touch_others_followup(self):
        fu = CallFollowUp.objects.create(
            employee=self.admin_emp, phone="123", scheduled_at=timezone.now()
        )
        resp = self.employee.post(
            reverse("clients:call_followup_update", args=[fu.id]), {"action": "done"}
        )
        self.assertEqual(resp.status_code, 403)


class FollowupScreenTests(_CallSetup):
    """What the Calls screen needs beyond a flat pending list: attempts,
    outcomes, last-call context, today's closed rows, bulk reschedule."""

    def _screen(self):
        return self.employee.get(reverse("clients:app_followups")).json()

    def _act(self, fu, body):
        return self.employee.post(
            reverse("clients:app_followup_action", args=[fu.id]),
            data=json.dumps(body), content_type="application/json",
        )

    def test_done_records_an_outcome_and_shows_in_today(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", scheduled_at=timezone.now()
        )
        self.assertEqual(
            self._act(fu, {"action": "done", "outcome": "not_interested"}).status_code, 200
        )
        fu.refresh_from_db()
        self.assertEqual(fu.outcome, "not_interested")

        data = self._screen()
        self.assertEqual(data["pending"], [])
        self.assertEqual(
            [(r["status"], r["outcome_label"]) for r in data["done_today"]],
            [("done", "Not interested")],
        )

    def test_unknown_outcome_is_ignored_not_stored(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now()
        )
        self._act(fu, {"action": "done", "outcome": "sold_them_a_boat"})
        fu.refresh_from_db()
        self.assertEqual(fu.outcome, "")

    def test_note_can_be_edited(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now(), note="old"
        )
        self._act(fu, {"action": "note", "note": "  wants a term plan  "})
        fu.refresh_from_db()
        self.assertEqual(fu.note, "wants a term plan")
        self.assertEqual(fu.status, CallFollowUp.STATUS_PENDING)  # still to be called

    def test_row_carries_last_call_and_attempts(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", client=self.customer,
            scheduled_at=timezone.now() + timedelta(hours=1), attempts=3,
        )
        CallLogEntry.objects.create(
            employee=self.emp, phone="+919876543210", direction="outgoing",
            connected=True, duration_seconds=185,
            started_at=timezone.now() - timedelta(days=2),
        )
        row = next(r for r in self._screen()["pending"] if r["id"] == fu.id)
        self.assertEqual(row["attempts"], 3)
        self.assertEqual(row["last_call"], "Last 2 days ago · 3m 05s")

    def test_overdue_count_drives_the_tab_badge(self):
        CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now() - timedelta(minutes=5)
        )
        CallFollowUp.objects.create(
            employee=self.emp, phone="2", scheduled_at=timezone.now() + timedelta(days=1)
        )
        self.assertEqual(self._screen()["overdue"], 1)
        self.assertEqual(
            self.employee.get(reverse("clients:app_me")).json()["overdue_followups"], 1
        )

    def test_push_overdue_moves_only_overdue_ones(self):
        overdue = CallFollowUp.objects.create(
            employee=self.emp, phone="1", reminded=True,
            scheduled_at=timezone.now() - timedelta(hours=2),
        )
        later = CallFollowUp.objects.create(
            employee=self.emp, phone="2", scheduled_at=timezone.now() + timedelta(days=2)
        )
        was = later.scheduled_at
        target = (timezone.localtime() + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        resp = self.employee.post(
            reverse("clients:app_followups_push_overdue"),
            data=json.dumps({"at": target.strftime("%Y-%m-%dT%H:%M")}),
            content_type="application/json",
        )
        self.assertEqual(resp.json()["moved"], 1)
        overdue.refresh_from_db(); later.refresh_from_db()
        self.assertEqual(timezone.localtime(overdue.scheduled_at), target)
        self.assertFalse(overdue.reminded)  # reminder re-arms
        self.assertEqual(later.scheduled_at, was)

    def test_push_overdue_rejects_a_past_moment(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now() - timedelta(hours=2)
        )
        before = fu.scheduled_at
        resp = self.employee.post(
            reverse("clients:app_followups_push_overdue"),
            data=json.dumps({"at": "2020-01-01T10:00"}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        fu.refresh_from_db()
        self.assertEqual(fu.scheduled_at, before)


class OutcomeReportTests(_CallSetup):
    """Outcomes are only worth recording if something reports on them."""

    def _close(self, phone, outcome):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone=phone, scheduled_at=timezone.now()
        )
        self.employee.post(
            reverse("clients:app_followup_action", args=[fu.id]),
            data=json.dumps({"action": "done", "outcome": outcome}),
            content_type="application/json",
        )
        return fu

    def test_breakdown_covers_every_outcome_including_zeros(self):
        self._close("1", "converted")
        self._close("2", "no_answer")
        self._close("3", "")  # closed without picking one

        from clients.services.calls import outcome_breakdown

        today = timezone.localdate()
        rows = {r["key"]: r["count"] for r in outcome_breakdown(today, today)}
        self.assertEqual(rows["converted"], 1)
        self.assertEqual(rows["no_answer"], 1)
        self.assertEqual(rows["not_interested"], 0)  # zeros are the finding too
        self.assertEqual(rows[""], 1)                 # "Not recorded"

    def test_web_and_app_analytics_show_the_same_numbers(self):
        self._close("1", "converted")
        web = self.admin.get(reverse("clients:call_analytics")).context["outcomes"]
        app = self.admin.get(
            reverse("clients:app_call_analytics"), {"range": "today"}
        ).json()["outcomes"]
        self.assertEqual(
            [(r["key"], r["count"]) for r in web],
            [(r["key"], r["count"]) for r in app],
        )

    def test_breakdown_can_be_scoped_to_one_employee(self):
        self._close("1", "converted")
        CallFollowUp.objects.create(
            employee=self.admin_emp, phone="9", scheduled_at=timezone.now(),
            status=CallFollowUp.STATUS_DONE, outcome="converted",
            completed_at=timezone.now(),
        )
        data = self.admin.get(
            reverse("clients:app_call_analytics"),
            {"range": "today", "employee_id": self.emp.id},
        ).json()
        row = next(r for r in data["outcomes"] if r["key"] == "converted")
        self.assertEqual(row["count"], 1)  # not 2

    def test_web_done_button_records_the_outcome(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now()
        )
        self.employee.post(
            reverse("clients:call_followup_update", args=[fu.id]),
            {"action": "done", "outcome": "converted"},
        )
        fu.refresh_from_db()
        self.assertEqual(fu.outcome, "converted")

    def test_web_note_can_be_edited(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now(), note="old"
        )
        self.employee.post(
            reverse("clients:call_followup_update", args=[fu.id]),
            {"action": "note", "note": "new note"},
        )
        fu.refresh_from_db()
        self.assertEqual(fu.note, "new note")
        self.assertEqual(fu.status, CallFollowUp.STATUS_PENDING)


class PopupContextTests(_CallSetup):
    """The popup used to ask about a number while showing nothing about it."""

    def test_context_line_names_the_client_and_the_history(self):
        CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", client=self.customer,
            scheduled_at=timezone.now(), attempts=3, note="wants term cover",
        )
        for i in range(2):
            CallLogEntry.objects.create(
                employee=self.emp, phone="+919876543210", direction="outgoing",
                connected=True, duration_seconds=60,
                started_at=timezone.now() - timedelta(hours=i + 1),
            )
        data = self.employee.get(
            reverse("clients:call_context"), {"phone": "+91 98765 43210"}
        ).json()
        self.assertIn("Callee", data["line"])
        self.assertIn("attempt 3", data["line"])
        self.assertIn("2 calls before this", data["line"])
        self.assertIn("wants term cover", data["line"])

    def test_unknown_number_returns_an_empty_line(self):
        data = self.employee.get(
            reverse("clients:call_context"), {"phone": "9000000000"}
        ).json()
        self.assertEqual(data["line"], "")
        self.assertEqual(data["pending_id"], 0)

    def test_not_interested_closes_the_number(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", scheduled_at=timezone.now()
        )
        other = CallFollowUp.objects.create(
            employee=self.emp, phone="9000000001", scheduled_at=timezone.now()
        )
        resp = self.employee.post(
            reverse("clients:call_close"),
            data=json.dumps({"phone": "+919876543210", "outcome": "not_interested"}),
            content_type="application/json",
        )
        self.assertEqual(resp.json()["closed_ids"], [fu.id])
        fu.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_DONE)
        self.assertEqual(fu.outcome, "not_interested")
        self.assertEqual(other.status, CallFollowUp.STATUS_PENDING)


class LeadFollowupsLeftTheCallsScreenTests(_CallSetup):
    """Lead follow-ups are Tasks now, worked on the Tasks screen. The Calls
    screen carries call follow-ups and nothing else — it used to merge in a
    second model whose row ids collided with these."""

    def setUp(self):
        super().setUp()
        from clients.models import Lead
        from clients.services import followups

        self.lead = Lead.objects.create(
            customer_name="Prospect", phone="9123456780", assigned_to=self.emp
        )
        self.task = followups.schedule(
            followups.LEAD, self.lead, timezone.now() + timedelta(hours=2),
            note="send brochure",
        )

    def test_only_call_rows_are_served(self):
        CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now() + timedelta(hours=1)
        )
        rows = self.employee.get(reverse("clients:app_followups")).json()["pending"]
        self.assertEqual([r["kind"] for r in rows], ["call"])

    def test_old_apps_asking_for_a_lead_action_are_told_to_update(self):
        resp = self.employee.post(
            reverse("clients:app_followup_action", args=[self.task.id]),
            data=json.dumps({"action": "done", "kind": "lead"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 410)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, self.task.STATUS_PENDING)

    def test_badge_counts_calls_only(self):
        CallFollowUp.objects.create(
            employee=self.emp, phone="1", scheduled_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(
            self.employee.get(reverse("clients:app_me")).json()["overdue_followups"], 1
        )


class AutoCloseOnCallTests(_CallSetup):
    """A connected outgoing call closes the follow-up it answers — the list
    used to fill with rows people had already called and forgotten to tick."""

    def _sync_call(self, phone, when, connected=True, direction="outgoing"):
        return self._sync(self.employee, [{
            "phone": phone, "direction": direction, "connected": connected,
            "duration_seconds": 90 if connected else 0,
            "started_at": int(when.timestamp() * 1000),
        }]).json()

    def test_connected_call_closes_the_followup(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210",
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        data = self._sync_call("+91 98765 43210", timezone.now() + timedelta(minutes=1))
        self.assertEqual(data["closed_followup_ids"], [fu.id])
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_DONE)
        self.assertEqual(fu.outcome, "spoke")

    def test_unanswered_call_leaves_it_pending(self):
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210", scheduled_at=timezone.now()
        )
        self._sync_call("9876543210", timezone.now(), connected=False)
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_PENDING)

    def test_call_before_the_followup_existed_does_not_close_it(self):
        """The post-call popup creates the next follow-up seconds after the
        call it followed; re-syncing that call must not wipe it out."""
        call_time = timezone.now()
        fu = CallFollowUp.objects.create(
            employee=self.emp, phone="9876543210",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        CallFollowUp.objects.filter(pk=fu.pk).update(created_at=call_time + timedelta(seconds=30))
        data = self._sync_call("9876543210", call_time)
        self.assertEqual(data["closed_followup_ids"], [])
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_PENDING)

    def test_another_employees_followup_is_untouched(self):
        fu = CallFollowUp.objects.create(
            employee=self.admin_emp, phone="9876543210", scheduled_at=timezone.now()
        )
        self._sync_call("9876543210", timezone.now() + timedelta(minutes=1))
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_PENDING)


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

    def _weekday_this_month(self, base, target_py_weekday):
        """A `target_py_weekday` (Mon=0..Sun=6) inside `base`'s own month.

        Walking *forward* from today put both days in next month whenever the
        run happened late in a month, and the analytics "month" range is the
        current calendar month — so the test passed or failed depending on the
        date it ran. Search from the 1st instead, which is always in-month.
        """
        d = base.replace(day=1)
        while d.weekday() != target_py_weekday:
            d += timedelta(days=1)
        return d

    def test_sunday_calls_excluded_from_analytics(self):
        cfg = CallTrackingSettings.current()
        cfg.work_days = "0,1,2,3,4,5"  # Mon–Sat, exclude Sunday
        cfg.save()
        base = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        saturday = self._weekday_this_month(base, 5)   # Sat
        sunday = self._weekday_this_month(base, 6)     # Sun
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

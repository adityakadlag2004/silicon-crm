"""Tests for the unified calendar: one common feed powering the calendar
page (FullCalendar JSON) and the dashboard agenda widget, plus the extended
reminder cron (lead follow-ups + calendar events + call follow-ups).

Run: .venv/bin/python manage.py test clients.test.test_unified_calendar
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    CalendarEvent,
    CallFollowUp,
    Client,
    Employee,
    Lead,
    LeadFollowUp,
    Notification,
    Task,
)
from clients.services import calendar_feed


def _mk_employee(username, role="employee"):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


class CalendarFeedServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("cal_emp")
        cls.other = _mk_employee("cal_other")
        now = timezone.now()

        cls.event = CalendarEvent.objects.create(
            employee=cls.emp, title="Review meeting", type="meeting",
            scheduled_time=now + timedelta(hours=2),
        )
        cls.lead = Lead.objects.create(customer_name="Ramesh", assigned_to=cls.emp)
        cls.lead_fu = LeadFollowUp.objects.create(
            lead=cls.lead, assigned_to=cls.emp,
            scheduled_time=now + timedelta(hours=3),
        )
        cls.call_fu = CallFollowUp.objects.create(
            employee=cls.emp, phone="9876543210",
            scheduled_at=now + timedelta(hours=4),
        )
        cls.task = Task.objects.create(
            title="Prepare report", assigned_to=cls.emp,
            due_date=(now + timedelta(days=1)).date(),
        )
        # someone else's items must not leak into a personal feed
        LeadFollowUp.objects.create(
            lead=cls.lead, assigned_to=cls.other,
            scheduled_time=now + timedelta(hours=5),
        )

    def test_feed_contains_all_sources(self):
        items = calendar_feed.feed_items(self.emp)
        sources = {it["source"] for it in items}
        self.assertEqual(
            sources, {"event", "lead_followup", "call_followup", "task"},
        )

    def test_personal_scope_excludes_other_employees(self):
        items = calendar_feed.feed_items(self.emp)
        lead_items = [it for it in items if it["source"] == "lead_followup"]
        self.assertEqual(len(lead_items), 1)
        self.assertEqual(lead_items[0]["key"], f"lead-{self.lead_fu.id}")

    def test_team_followups_includes_everyone(self):
        items = calendar_feed.feed_items(self.emp, team_followups=True)
        lead_items = [it for it in items if it["source"] == "lead_followup"]
        self.assertEqual(len(lead_items), 2)

    def test_overdue_flag(self):
        LeadFollowUp.objects.create(
            lead=self.lead, assigned_to=self.emp,
            scheduled_time=timezone.now() - timedelta(days=1),
        )
        items = calendar_feed.feed_items(self.emp)
        overdue = [it for it in items if it["is_overdue"]]
        self.assertEqual(len(overdue), 1)

    def test_fullcalendar_mapping_marks_only_events_editable(self):
        items = calendar_feed.feed_items(self.emp)
        fc = calendar_feed.to_fullcalendar(items)
        by_editable = {e["extendedProps"]["source"]: e["editable"] for e in fc}
        self.assertTrue(by_editable["event"])
        self.assertFalse(by_editable["lead_followup"])
        self.assertFalse(by_editable["call_followup"])
        self.assertFalse(by_editable["task"])
        # manual events keep their integer id for the edit/delete endpoints
        ev = next(e for e in fc if e["extendedProps"]["source"] == "event")
        self.assertEqual(ev["id"], str(self.event.id))


class CalendarEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("cal_web_emp")
        cls.admin = _mk_employee("cal_web_admin", role="admin")
        now = timezone.now()
        cls.lead = Lead.objects.create(customer_name="Suresh", assigned_to=cls.emp)
        LeadFollowUp.objects.create(
            lead=cls.lead, assigned_to=cls.emp,
            scheduled_time=now + timedelta(hours=1),
        )
        CalendarEvent.objects.create(
            employee=cls.emp, title="My event", type="meeting",
            scheduled_time=now + timedelta(hours=1),
        )

    def _http(self, emp):
        c = TestClient()
        c.force_login(emp.user)
        return c

    def test_events_json_returns_unified_feed(self):
        data = self._http(self.emp).get(reverse("clients:calendar_events_json")).json()
        sources = {e["extendedProps"]["source"] for e in data}
        self.assertIn("event", sources)
        self.assertIn("lead_followup", sources)

    def test_events_json_source_filter(self):
        data = self._http(self.emp).get(
            reverse("clients:calendar_events_json"), {"sources": "lead_followup"}
        ).json()
        self.assertTrue(data)
        self.assertTrue(all(e["extendedProps"]["source"] == "lead_followup" for e in data))

    def test_agenda_employee_sees_own_items(self):
        data = self._http(self.emp).get(reverse("clients:dashboard_agenda_json")).json()
        self.assertEqual(len(data["items"]), 2)
        self.assertIn("week_dates", data)

    def test_agenda_admin_sees_team_followups(self):
        data = self._http(self.admin).get(reverse("clients:dashboard_agenda_json")).json()
        lead_items = [it for it in data["items"] if it["source"] == "lead_followup"]
        self.assertEqual(len(lead_items), 1)  # the employee's follow-up

    def test_agenda_overdue_filter(self):
        LeadFollowUp.objects.create(
            lead=self.lead, assigned_to=self.emp,
            scheduled_time=timezone.now() - timedelta(days=2),
        )
        data = self._http(self.emp).get(
            reverse("clients:dashboard_agenda_json"), {"filter": "overdue"}
        ).json()
        self.assertEqual(len(data["items"]), 1)
        self.assertTrue(data["items"][0]["is_overdue"])


class UnifiedReminderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("rem_emp")
        cls.lead = Lead.objects.create(customer_name="Mahesh", assigned_to=cls.emp)

    def test_due_lead_followup_creates_notification_once(self):
        fu = LeadFollowUp.objects.create(
            lead=self.lead, assigned_to=self.emp,
            scheduled_time=timezone.now() - timedelta(minutes=5),
        )
        call_command("send_followup_reminders")
        note = Notification.objects.get(recipient=self.emp.user)
        self.assertIn("Mahesh", note.title)
        self.assertEqual(note.link, reverse("clients:lead_detail", args=[self.lead.id]))
        fu.refresh_from_db()
        self.assertTrue(fu.reminded)
        # second run must not duplicate
        call_command("send_followup_reminders")
        self.assertEqual(Notification.objects.filter(recipient=self.emp.user).count(), 1)

    def test_due_event_creates_notification(self):
        CalendarEvent.objects.create(
            employee=self.emp, title="Client review", type="meeting",
            scheduled_time=timezone.now() - timedelta(minutes=1),
        )
        call_command("send_followup_reminders")
        note = Notification.objects.get(recipient=self.emp.user)
        self.assertIn("Client review", note.title)

    def test_future_items_not_reminded(self):
        LeadFollowUp.objects.create(
            lead=self.lead, assigned_to=self.emp,
            scheduled_time=timezone.now() + timedelta(hours=1),
        )
        call_command("send_followup_reminders")
        self.assertEqual(Notification.objects.count(), 0)

    def test_due_call_followup_still_works(self):
        CallFollowUp.objects.create(
            employee=self.emp, phone="9123456789",
            scheduled_at=timezone.now() - timedelta(minutes=2),
        )
        call_command("send_followup_reminders")
        note = Notification.objects.get(recipient=self.emp.user)
        self.assertTrue(note.link.startswith("tel:"))

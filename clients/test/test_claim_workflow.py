"""Claim workflow: raise, advance stages, notes, documents, reminders."""
from datetime import date

from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Client, ClaimActivity, Employee, InsuranceClaim,
    InsurancePolicy, Task,
)
from clients.services import claims as claims_service
from clients.services import followups


class ClaimWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("cl_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=7100, name="Vishal Bose")
        cls.policy = InsurancePolicy.objects.create(
            client=cls.client_rec, policy_number="INS999", insurer="ICICI Lombard",
            insurance_type=InsurancePolicy.TYPE_HEALTH)

    def _tc(self):
        tc = TC(); tc.force_login(self.admin_user); return tc

    def test_raise_claim_from_policy(self):
        tc = self._tc()
        resp = tc.post(reverse("clients:raise_claim_for_policy", args=[self.policy.id]), {
            "claim_type": "Hospitalisation Claim", "claim_mode": "cashless",
            "status": "intimated", "claimed_amount": "245500", "settled_amount": "0",
        })
        self.assertEqual(resp.status_code, 302)
        claim = InsuranceClaim.objects.get(policy=self.policy)
        self.assertEqual(claim.claim_type, "Hospitalisation Claim")
        self.assertEqual(claim.intimation_date, timezone.localdate())
        # A "created" activity is logged.
        self.assertTrue(claim.activities.filter(action=ClaimActivity.CREATED).exists())

    def _claim(self, **kw):
        d = dict(policy=self.policy, claim_type="Hospitalisation Claim",
                 status=InsuranceClaim.STATUS_INTIMATED, claimed_amount=245500,
                 handled_by=self.emp)
        d.update(kw)
        return InsuranceClaim.objects.create(**d)

    def test_advance_stage_stamps_date_and_logs(self):
        claim = self._claim()
        claims_service.advance_stage(claim, InsuranceClaim.STATUS_SUBMITTED, self.admin_user)
        claim.refresh_from_db()
        self.assertEqual(claim.status, InsuranceClaim.STATUS_SUBMITTED)
        self.assertEqual(claim.submission_date, timezone.localdate())
        self.assertTrue(claim.activities.filter(action=ClaimActivity.STATUS_CHANGED).exists())

    def test_settling_records_the_settled_amount(self):
        claim = self._claim()
        claims_service.advance_stage(claim, InsuranceClaim.STATUS_SETTLED,
                                     self.admin_user, settled_amount=218750)
        claim.refresh_from_db()
        self.assertEqual(claim.status, InsuranceClaim.STATUS_SETTLED)
        self.assertEqual(claim.settled_amount, 218750)
        self.assertEqual(claim.settlement_date, timezone.localdate())
        self.assertEqual(claim.shortfall, 245500 - 218750)

    def test_stage_update_via_view_with_reminder(self):
        claim = self._claim()
        tc = self._tc()
        tc.post(reverse("clients:claim_update_status", args=[claim.id]), {
            "status": "submitted", "note": "Docs sent to insurer",
            "reminder_at": "2026-08-01T10:00", "reminder_note": "Chase the insurer",
        })
        claim.refresh_from_db()
        self.assertEqual(claim.status, InsuranceClaim.STATUS_SUBMITTED)
        # A reminder was scheduled from the same form — as a task.
        task = followups.for_source(followups.CLAIM, claim.pk).first()
        self.assertIsNotNone(task)
        self.assertIn("Chase the insurer", task.description)
        self.assertEqual(task.title, "Claim follow-up — Vishal Bose")
        self.assertEqual(task.due_date, date(2026, 8, 1))

    def test_add_note_appears_in_timeline(self):
        claim = self._claim()
        tc = self._tc()
        tc.post(reverse("clients:claim_add_note", args=[claim.id]), {"note": "Client called"})
        self.assertTrue(claim.activities.filter(action=ClaimActivity.NOTE, detail="Client called").exists())

    def test_reminder_completion(self):
        claim = self._claim()
        rem = claims_service.create_reminder(
            claim, self.admin_user, timezone.now() + timezone.timedelta(days=1), note="x")
        tc = self._tc()
        # A follow-up closes where every other task closes.
        tc.post(reverse("clients:task_set_status", args=[rem.id]))
        rem.refresh_from_db()
        self.assertEqual(rem.status, Task.STATUS_COMPLETED)

    def test_claim_detail_renders_workspace(self):
        claim = self._claim()
        claims_service.add_note(claim, self.admin_user, "a note")
        html = self._tc().get(reverse("clients:claim_detail", args=[claim.id])).content.decode()
        for frag in ("Update stage", "Documents", "Follow-ups", "Timeline", "ki-steps"):
            self.assertIn(frag, html)


class ClaimReminderPipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("cr_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=7200, name="Rahul Sharma")
        cls.policy = InsurancePolicy.objects.create(
            client=cls.client_rec, policy_number="INS888", insurer="Star",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        cls.claim = InsuranceClaim.objects.create(
            policy=cls.policy, claim_type="Claim", handled_by=cls.emp)

    def test_due_reminder_rings_through_the_task_pipeline(self):
        """It used to be a tray notification from send_followup_reminders.
        A claim follow-up is a task, so tasks_ring_due rings it like an alarm."""
        from io import StringIO
        from django.core.management import call_command

        due = timezone.localtime().replace(second=0, microsecond=0)
        task = claims_service.create_reminder(
            self.claim, self.admin_user, due, note="Chase insurer")

        call_command("tasks_ring_due", stdout=StringIO())
        task.refresh_from_db()
        self.assertIsNotNone(task.due_alarm_sent_at)

        # And the old pipeline no longer knows anything about claims.
        out = StringIO()
        call_command("send_followup_reminders", stdout=out)
        self.assertNotIn("Claim", out.getvalue())

    def test_reminder_appears_on_the_calendar_feed_as_a_task(self):
        from clients.services import calendar_feed
        soon = timezone.now() + timezone.timedelta(days=2)
        claims_service.create_reminder(self.claim, self.admin_user, soon, note="Follow up")
        items = calendar_feed.feed_items(
            self.emp, start=timezone.now(), end=timezone.now() + timezone.timedelta(days=5),
            sources=["task"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "task")
        self.assertIn("Rahul Sharma", items[0]["title"])

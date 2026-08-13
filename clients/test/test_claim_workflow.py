"""Claim workflow: raise, advance stages, notes, documents, reminders."""
from datetime import date

from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Client, ClaimActivity, ClaimReminder, Employee, InsuranceClaim,
    InsurancePolicy, Notification,
)
from clients.services import claims as claims_service


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
        # A reminder was scheduled from the same form.
        rem = ClaimReminder.objects.filter(claim=claim).first()
        self.assertIsNotNone(rem)
        self.assertEqual(rem.note, "Chase the insurer")

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
        tc.post(reverse("clients:claim_reminder_done", args=[rem.id]))
        rem.refresh_from_db()
        self.assertEqual(rem.status, ClaimReminder.STATUS_DONE)

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

    def test_due_reminder_pushes_a_notification(self):
        from io import StringIO
        from django.core.management import call_command
        past = timezone.now() - timezone.timedelta(minutes=5)
        ClaimReminder.objects.create(claim=self.claim, employee=self.emp,
                                     scheduled_at=past, note="Chase insurer")
        call_command("send_followup_reminders", stdout=StringIO())
        note = Notification.objects.filter(recipient=self.admin_user).first()
        self.assertIsNotNone(note)
        self.assertIn("Claim follow-up", note.title)
        # Fires exactly once.
        ClaimReminder.objects.get().refresh_from_db()
        self.assertTrue(ClaimReminder.objects.get().reminded)

    def test_reminder_appears_on_the_calendar_feed(self):
        from clients.services import calendar_feed
        soon = timezone.now() + timezone.timedelta(days=2)
        ClaimReminder.objects.create(claim=self.claim, employee=self.emp,
                                     scheduled_at=soon, note="Follow up")
        items = calendar_feed.feed_items(
            self.emp, start=timezone.now(), end=timezone.now() + timezone.timedelta(days=5),
            sources=["claim_reminder"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "claim_reminder")
        self.assertIn("Rahul Sharma", items[0]["title"])

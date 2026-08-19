"""Claim workflow: raise, advance stages, notes, documents, reminders."""
import json as _json
from datetime import date, timedelta

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


class AppInsuranceApiTests(TestCase):
    """The app's Insurance module — the same workflow, from the phone.

    A claim is intimated over the phone and its papers are photographed on the
    spot, so every write here must land through `services/claims.py` and leave
    the same trail the web screens do.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("app_ins", password="pw")
        cls.emp = Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)
        cls.cust = Client.objects.create(id=7200, name="Nikhil Rao", phone="9876500042")
        cls.policy = InsurancePolicy.objects.create(
            client=cls.cust, policy_number="APP123", insurer="Star Health",
            insurance_type=InsurancePolicy.TYPE_HEALTH, sum_insured=500000,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))

    def _tc(self):
        tc = TC(); tc.force_login(self.user); return tc

    def test_policies_list_carries_counts_and_search(self):
        data = self._tc().get(reverse("clients:app_policies")).json()
        self.assertEqual(data["counts"]["total"], 1)
        self.assertEqual(data["results"][0]["number"], "APP123")
        self.assertEqual(data["results"][0]["client"], "Nikhil Rao")
        hit = self._tc().get(reverse("clients:app_policies") + "?q=star").json()
        self.assertEqual(len(hit["results"]), 1)
        miss = self._tc().get(reverse("clients:app_policies") + "?q=zzz").json()
        self.assertEqual(miss["results"], [])

    def test_raising_a_claim_logs_it_and_defaults_the_handler(self):
        resp = self._tc().post(
            reverse("clients:app_claim_create"),
            data=_json.dumps({
                "policy_id": self.policy.id, "claim_type": "Hospitalisation",
                "claim_mode": "reimbursement", "claimed_amount": "120000",
                "note": "Admitted last night.",
            }),
            content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        claim = InsuranceClaim.objects.get(pk=resp.json()["id"])
        self.assertEqual(claim.handled_by, self.emp)
        self.assertEqual(claim.intimation_date, timezone.localdate())
        self.assertEqual(claim.claimed_amount, 120000)
        self.assertTrue(claim.activities.filter(action=ClaimActivity.CREATED).exists())
        self.assertTrue(claim.activities.filter(action=ClaimActivity.NOTE).exists())

    def _claim(self):
        return InsuranceClaim.objects.create(
            policy=self.policy, claim_type="Hospitalisation", handled_by=self.emp,
            status=InsuranceClaim.STATUS_INTIMATED, claimed_amount=120000)

    def test_update_advances_the_stage_and_stamps_the_date(self):
        claim = self._claim()
        self._tc().post(
            reverse("clients:app_claim_update", args=[claim.pk]),
            data=_json.dumps({"status": InsuranceClaim.STATUS_SUBMITTED,
                              "note": "Papers couriered."}),
            content_type="application/json")
        claim.refresh_from_db()
        self.assertEqual(claim.status, InsuranceClaim.STATUS_SUBMITTED)
        self.assertEqual(claim.submission_date, timezone.localdate())

    def test_settling_from_the_app_records_the_amount(self):
        claim = self._claim()
        self._tc().post(
            reverse("clients:app_claim_update", args=[claim.pk]),
            data=_json.dumps({"status": InsuranceClaim.STATUS_SETTLED,
                              "settled_amount": "95000"}),
            content_type="application/json")
        claim.refresh_from_db()
        self.assertEqual(float(claim.settled_amount), 95000.0)
        self.assertEqual(float(claim.shortfall), 25000.0)

    def test_a_followup_from_the_app_is_a_task(self):
        claim = self._claim()
        when = timezone.localtime() + timedelta(days=2)
        self._tc().post(
            reverse("clients:app_claim_update", args=[claim.pk]),
            data=_json.dumps({"note": "Chase the TPA.",
                              "reminder_at": when.strftime("%Y-%m-%dT%H:%M"),
                              "reminder_note": "Ask for the query letter"}),
            content_type="application/json")
        task = Task.objects.get(source_kind=followups.CLAIM, source_id=claim.pk)
        self.assertEqual(task.due_date, when.date())
        self.assertEqual(task.assigned_to, self.emp)

    def test_claim_detail_serves_the_timeline_and_the_reminders(self):
        claim = self._claim()
        claims_service.add_note(claim, self.user, "Called the hospital desk.")
        data = self._tc().get(reverse("clients:app_claim_detail", args=[claim.pk])).json()
        self.assertEqual(data["client"], "Nikhil Rao")
        self.assertEqual(data["policy_number"], "APP123")
        self.assertTrue(data["is_open"])
        self.assertIn("Called the hospital desk.",
                      [a["detail"] for a in data["activities"]])

    def test_open_filter_leaves_settled_claims_out(self):
        claim = self._claim()
        claims_service.advance_stage(claim, InsuranceClaim.STATUS_SETTLED, self.user)
        data = self._tc().get(reverse("clients:app_claims") + "?status=open").json()
        self.assertEqual(data["results"], [])
        self.assertEqual(data["counts"]["settled"], 1)

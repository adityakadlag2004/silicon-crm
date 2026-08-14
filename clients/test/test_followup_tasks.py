"""A follow-up is a Task — on a lead, on a claim, on anything dated.

Pins the contract in services/followups.py: who it's created by, who it's
assigned to, that it rings through the task pipeline, and that it stops
ringing when the record it chases is finished with.

Run: .venv/bin/python manage.py test clients.test.test_followup_tasks
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client, Employee, InsuranceClaim, InsurancePolicy, Lead, Task
from clients.services import followups
from clients.services import leads as lead_service


def _employee(username, role="employee"):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


class FollowupTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("fu_emp")
        cls.admin = _employee("fu_admin", role="admin")
        cls.lead = Lead.objects.create(
            customer_name="Ramesh Pawar", phone="9876500011",
            assigned_to=cls.emp, stage=Lead.STAGE_NEGOTIATION,
        )

    def _schedule(self, when=None, **kw):
        return followups.schedule(
            followups.LEAD, self.lead,
            when or timezone.now() + timedelta(days=1),
            note="Send the quote", **kw)

    # ── who is who ──────────────────────────────────────────────────────────

    def test_created_by_the_system_user_never_the_person(self):
        task = self._schedule(actor=self.admin.user)
        self.assertEqual(task.created_by.username, "system")
        self.assertFalse(task.created_by.is_active)
        self.assertFalse(task.created_by.has_usable_password())

    def test_assigned_to_the_lead_owner_not_the_admin_who_scheduled_it(self):
        """An admin chasing someone else's lead must ring THAT phone."""
        task = self._schedule(actor=self.admin.user)
        self.assertEqual(task.assigned_to, self.emp)

    def test_falls_back_to_the_scheduler_when_the_record_has_no_owner(self):
        claim_less_owner = InsuranceClaim.objects.create(
            policy=InsurancePolicy.objects.create(
                client=Client.objects.create(id=7411, name="Unhandled"),
                policy_number="INS411", insurer="Star",
                insurance_type=InsurancePolicy.TYPE_HEALTH),
            claim_type="Claim")  # nobody handling it yet
        task = followups.schedule(followups.CLAIM, claim_less_owner,
                                  timezone.now() + timedelta(days=1),
                                  actor=self.admin.user)
        self.assertEqual(task.assigned_to, self.admin)

    # ── what it says ────────────────────────────────────────────────────────

    def test_title_names_the_pipeline_and_the_client(self):
        task = self._schedule()
        self.assertEqual(task.title, "SPANCO Lead follow-up — Ramesh Pawar")
        self.assertIn("Negotiation", task.description)
        self.assertIn("9876500011", task.description)
        self.assertIn("Send the quote", task.description)

    def test_carries_the_due_time_so_it_can_ring_on_the_minute(self):
        when = (timezone.localtime() + timedelta(days=1)).replace(
            hour=15, minute=30, second=17, microsecond=0)
        task = self._schedule(when=when)
        self.assertEqual(task.due_date, when.date())
        self.assertEqual(task.due_time.hour, 15)
        self.assertEqual(task.due_time.minute, 30)
        self.assertEqual(task.due_time.second, 0)

    def test_lands_in_the_followup_category_so_the_task_list_stays_readable(self):
        self.assertEqual(self._schedule().category.name, "Follow-up")

    def test_medium_priority_by_default(self):
        # High/critical re-ring every four hours until acknowledged; twenty
        # follow-ups a day at that volume is a storm people swipe away.
        self.assertEqual(self._schedule().priority, Task.PRIORITY_MEDIUM)

    # ── it actually rings ───────────────────────────────────────────────────

    def test_the_task_pipeline_rings_it_at_its_due_minute(self):
        due = timezone.localtime().replace(second=0, microsecond=0)
        task = self._schedule(when=due)
        call_command("tasks_ring_due", stdout=StringIO())
        task.refresh_from_db()
        self.assertIsNotNone(task.due_alarm_sent_at)

    # ── the loop closes ─────────────────────────────────────────────────────

    def test_a_lost_lead_stops_being_chased(self):
        task = self._schedule()
        lead_service.mark_lost(self.lead, user=self.admin.user, reason="Went elsewhere")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_CANCELLED)

    def test_a_converted_lead_stops_being_chased(self):
        task = self._schedule()
        lead_service.set_stage(self.lead, Lead.STAGE_ORDER, user=self.admin.user)
        lead_service.convert_to_client(self.lead, user=self.admin.user)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_CANCELLED)

    def test_a_completed_followup_is_left_alone_when_the_lead_closes(self):
        task = self._schedule()
        task.mark_completed()
        lead_service.mark_lost(self.lead, user=self.admin.user, reason="x")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_COMPLETED)

    def test_deleting_the_lead_takes_its_followups_with_it(self):
        """Nothing cascades — the link is source_kind/source_id, not an FK —
        so a deleted lead would otherwise leave a phone ringing."""
        task = self._schedule()
        self.lead.delete()
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_for_source_lists_only_that_records_followups(self):
        mine = self._schedule()
        other_lead = Lead.objects.create(customer_name="Someone Else", assigned_to=self.emp)
        followups.schedule(followups.LEAD, other_lead, timezone.now() + timedelta(days=1))
        self.assertEqual([t.pk for t in followups.for_source(followups.LEAD, self.lead.pk)],
                         [mine.pk])


class FollowupThroughTheLeadPageTests(TestCase):
    """The web path end to end: add on the lead page, close as a task."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("fu_web")
        cls.lead = Lead.objects.create(customer_name="Kiran Shah", assigned_to=cls.emp)

    def _http(self):
        http = TestClient()
        http.force_login(self.emp.user)
        return http

    def test_adding_a_followup_creates_the_task(self):
        when = (timezone.localtime() + timedelta(days=2)).replace(
            hour=10, minute=0, second=0, microsecond=0)
        self._http().post(
            reverse("clients:lead_add_followup", args=[self.lead.id]),
            {"scheduled_time": when.strftime("%Y-%m-%dT%H:%M"), "note": "Call back"},
        )
        task = followups.for_source(followups.LEAD, self.lead.pk).get()
        self.assertEqual(task.assigned_to, self.emp)
        self.assertEqual(task.due_date, when.date())

    def test_the_lead_page_lists_its_followups(self):
        followups.schedule(followups.LEAD, self.lead, timezone.now() + timedelta(days=1),
                           note="Bring the illustration")
        html = self._http().get(reverse("clients:lead_detail", args=[self.lead.id])).content.decode()
        self.assertIn("Bring the illustration", html)
        self.assertIn("rings on the phone", html)

    def test_a_bare_status_post_closes_it(self):
        task = followups.schedule(followups.LEAD, self.lead, timezone.now() + timedelta(days=1))
        self._http().post(reverse("clients:task_set_status", args=[task.id]))
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_COMPLETED)

    def test_rescheduling_re_arms_the_ring(self):
        """A rescheduled task that never rings again is worse than no reminder."""
        task = followups.schedule(followups.LEAD, self.lead, timezone.now() - timedelta(hours=1))
        Task.objects.filter(pk=task.pk).update(
            due_alarm_sent_at=timezone.now(), reminded_same_day=True)
        new_date = timezone.localdate() + timedelta(days=3)
        resp = self._http().post(
            reverse("clients:task_reschedule", args=[task.id]),
            data='{"date": "%s"}' % new_date.isoformat(),
            content_type="application/json",
        )
        self.assertEqual(resp.json()["new_date"], new_date.isoformat())
        task.refresh_from_db()
        self.assertEqual(task.due_date, new_date)
        self.assertIsNone(task.due_alarm_sent_at)
        self.assertFalse(task.reminded_same_day)


class ClaimFollowupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _employee("fu_claim")
        cls.client_rec = Client.objects.create(id=7300, name="Neha Kulkarni")
        cls.policy = InsurancePolicy.objects.create(
            client=cls.client_rec, policy_number="INS777", insurer="Star",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        cls.claim = InsuranceClaim.objects.create(
            policy=cls.policy, claim_type="Hospitalisation", handled_by=cls.emp)

    def test_a_claim_followup_is_a_task_on_the_handler(self):
        task = followups.schedule(followups.CLAIM, self.claim,
                                  timezone.now() + timedelta(days=1), note="Chase TPA")
        self.assertEqual(task.title, "Claim follow-up — Neha Kulkarni")
        self.assertEqual(task.assigned_to, self.emp)
        self.assertEqual(task.client, self.client_rec)
        self.assertIn("INS777", task.description)
        self.assertIn("Chase TPA", task.description)

    def test_deleting_the_claim_takes_its_followups_with_it(self):
        task = followups.schedule(followups.CLAIM, self.claim,
                                  timezone.now() + timedelta(days=1))
        self.claim.delete()
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

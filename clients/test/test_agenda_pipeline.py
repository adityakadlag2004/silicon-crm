"""The agenda shows the whole team's follow-ups, says what kind each is, and
ranks the live pipeline first — plus the panel for leads nobody has dated.

Three separate defects are pinned here:

  * an admin's agenda lost every other employee's lead follow-up when
    follow-ups became Tasks (`_tasks` was hard-scoped to one employee while
    `_call_followups` honoured team scope);
  * every commitment read "Task", so a call could not be told from a chase;
  * `insurance_renewal` had no checkbox on the calendar page, so the source
    was unreachable there, and no badge colour on the agenda.

Run: .venv/bin/python manage.py test clients.test.test_agenda_pipeline
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import CallFollowUp, Lead, Employee, Task
from clients.services import calendar_feed, followups, leads as lead_service


def _mk_employee(username, role="employee"):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


class TeamScopeTests(TestCase):
    """An admin dashboard sees the team's follow-ups, not only their own."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk_employee("ag_admin", role="admin")
        cls.seller = _mk_employee("ag_seller")
        now = timezone.now()
        cls.lead = Lead.objects.create(
            customer_name="Priya", assigned_to=cls.seller,
            stage=Lead.STAGE_NEGOTIATION)
        cls.lead_task = followups.schedule(followups.LEAD, cls.lead, now + timedelta(hours=2))
        cls.call = CallFollowUp.objects.create(
            employee=cls.seller, phone="9876500000", scheduled_at=now + timedelta(hours=3))

    def test_personal_feed_excludes_other_peoples_tasks(self):
        keys = {it["key"] for it in calendar_feed.feed_items(self.admin)}
        self.assertNotIn(f"task-{self.lead_task.id}", keys)

    def test_team_feed_includes_another_employees_lead_followup(self):
        items = calendar_feed.feed_items(self.admin, team_followups=True)
        keys = {it["key"] for it in items}
        self.assertIn(f"task-{self.lead_task.id}", keys)
        self.assertIn(f"call-{self.call.id}", keys)

    def test_employee_filter_narrows_tasks_too(self):
        other = _mk_employee("ag_other")
        other_task = Task.objects.create(
            title="Other's chore", assigned_to=other,
            due_date=timezone.localdate() + timedelta(days=1))

        items = calendar_feed.feed_items(
            self.admin, team_followups=True, employee_id=self.seller.id)
        keys = {it["key"] for it in items}
        self.assertIn(f"task-{self.lead_task.id}", keys)
        self.assertNotIn(f"task-{other_task.id}", keys)

    def test_admin_dashboard_endpoint_serves_the_team(self):
        client = TestClient()
        client.force_login(self.admin.user)
        res = client.get(reverse("clients:dashboard_agenda_json"), {"filter": "all"})
        keys = {row["key"] for row in res.json()["items"]}
        self.assertIn(f"task-{self.lead_task.id}", keys)


class BadgeAndStageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("ag_badge")
        now = timezone.now()
        cls.lead = Lead.objects.create(
            customer_name="Anil", assigned_to=cls.emp, stage=Lead.STAGE_NEGOTIATION)
        cls.lead_task = followups.schedule(followups.LEAD, cls.lead, now + timedelta(hours=1))
        cls.chore = Task.objects.create(
            title="Order stationery", assigned_to=cls.emp,
            due_date=timezone.localdate() + timedelta(days=1))

    def _by_key(self, key):
        return next(it for it in calendar_feed.feed_items(self.emp) if it["key"] == key)

    def test_a_lead_followup_is_labelled_lead_not_task(self):
        self.assertEqual(self._by_key(f"task-{self.lead_task.id}")["source_label"], "Lead")

    def test_a_plain_task_is_still_labelled_task(self):
        self.assertEqual(self._by_key(f"task-{self.chore.id}")["source_label"], "Task")

    def test_a_lead_followup_carries_its_spanco_stage(self):
        item = self._by_key(f"task-{self.lead_task.id}")
        self.assertEqual(item["stage"], Lead.STAGE_NEGOTIATION)
        self.assertIsNone(self._by_key(f"task-{self.chore.id}")["stage"])

    def test_agenda_json_exposes_the_stage_colour_and_hotness(self):
        rows = {r["key"]: r for r in calendar_feed.to_agenda_json(
            calendar_feed.feed_items(self.emp))}
        lead_row = rows[f"task-{self.lead_task.id}"]
        self.assertTrue(lead_row["hot"])
        self.assertEqual(lead_row["stage_color"], Lead.STAGE_COLORS[Lead.STAGE_NEGOTIATION])
        self.assertFalse(rows[f"task-{self.chore.id}"]["hot"])

    def test_a_live_pipeline_chase_sorts_above_a_chore_on_the_same_day(self):
        due = timezone.localdate() + timedelta(days=3)
        chore = Task.objects.create(
            title="Early chore", assigned_to=self.emp, due_date=due,
            due_time=timezone.datetime.min.time().replace(hour=9))
        hot_lead = Lead.objects.create(
            customer_name="Late but hot", assigned_to=self.emp, stage=Lead.STAGE_CONCLUSION)
        chase = followups.schedule(
            followups.LEAD, hot_lead,
            timezone.make_aware(timezone.datetime.combine(
                due, timezone.datetime.min.time().replace(hour=17))))

        day = [it for it in calendar_feed.feed_items(self.emp)
               if timezone.localdate(it["start"]) == due]
        self.assertEqual(day[0]["key"], f"task-{chase.id}",
                         "the Conclusion-stage chase must lead the day, not the 9am chore")
        self.assertIn(f"task-{chore.id}", [it["key"] for it in day])


class SourceFilterTests(TestCase):
    """The agenda chips pass `sources` through to the feed."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("ag_src")
        now = timezone.now()
        cls.call = CallFollowUp.objects.create(
            employee=cls.emp, phone="9000000000", scheduled_at=now + timedelta(hours=1))
        cls.task = Task.objects.create(
            title="A task", assigned_to=cls.emp,
            due_date=timezone.localdate() + timedelta(days=1))

    def test_sources_param_narrows_the_agenda(self):
        client = TestClient()
        client.force_login(self.emp.user)
        res = client.get(reverse("clients:dashboard_agenda_json"),
                         {"filter": "all", "sources": "call_followup"})
        sources = {row["source"] for row in res.json()["items"]}
        self.assertEqual(sources, {"call_followup"})

    def test_no_sources_param_serves_everything(self):
        client = TestClient()
        client.force_login(self.emp.user)
        res = client.get(reverse("clients:dashboard_agenda_json"), {"filter": "all"})
        sources = {row["source"] for row in res.json()["items"]}
        self.assertIn("task", sources)
        self.assertIn("call_followup", sources)


class RenewalSourceReachableTests(TestCase):
    """Every source must be reachable and coloured on both surfaces."""

    def test_calendar_page_offers_the_insurance_renewal_source(self):
        emp = _mk_employee("ag_cal")
        client = TestClient()
        client.force_login(emp.user)
        html = client.get(reverse("clients:employee_calendar_page")).content.decode()
        # Without this checkbox the page's `sources` param never contains
        # insurance_renewal, so policy renewals are invisible there.
        self.assertIn('class="form-check-input src-filter" type="checkbox" '
                      'id="src_renewal" value="insurance_renewal"', html)
        self.assertIn("insurance_renewal: {bg:", html)

    def test_every_agenda_source_has_a_badge_colour(self):
        with open("templates/dashboards/_followup_calendar.html") as fh:
            css = fh.read()
        for source in calendar_feed.ALL_SOURCES:
            self.assertIn(f".agenda-source-{source}", css,
                          f"{source} renders an unstyled badge")


class NeedsAttentionTests(TestCase):
    """Leads with nothing dated — the ones no calendar can show."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("ag_att")

    def _lead(self, name, stage, *, moved_days_ago=0, lost=False):
        lead = Lead.objects.create(customer_name=name, assigned_to=self.emp,
                                   stage=stage, is_discarded=lost)
        Lead.objects.filter(pk=lead.pk).update(
            stage_changed_at=timezone.now() - timedelta(days=moved_days_ago))
        lead.refresh_from_db()
        return lead

    def test_a_hot_lead_with_no_followup_is_listed(self):
        lead = self._lead("Unchased", Lead.STAGE_NEGOTIATION)
        rows, total = lead_service.needs_attention(Lead.objects.all())
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["lead"], lead)
        self.assertTrue(rows[0]["no_followup"])

    def test_scheduling_a_followup_takes_the_lead_off_the_list(self):
        lead = self._lead("Chased", Lead.STAGE_NEGOTIATION)
        followups.schedule(followups.LEAD, lead, timezone.now() + timedelta(days=1))
        rows, total = lead_service.needs_attention(Lead.objects.all())
        self.assertEqual(total, 0, "a lead with an open follow-up is already handled")

    def test_a_chased_but_stalled_lead_is_still_listed(self):
        lead = self._lead("Stuck", Lead.STAGE_APPROACH, moved_days_ago=40)
        followups.schedule(followups.LEAD, lead, timezone.now() + timedelta(days=1))
        rows, total = lead_service.needs_attention(Lead.objects.all())
        self.assertEqual(total, 1)
        self.assertTrue(rows[0]["stalled"])
        self.assertFalse(rows[0]["no_followup"])

    def test_cold_and_booked_stages_are_left_alone(self):
        self._lead("Too early", Lead.STAGE_SUSPECT)
        self._lead("Also early", Lead.STAGE_PROSPECT)
        self._lead("Booked", Lead.STAGE_ORDER)
        _, total = lead_service.needs_attention(Lead.objects.all())
        self.assertEqual(total, 0)

    def test_lost_leads_are_not_chased(self):
        self._lead("Gone", Lead.STAGE_NEGOTIATION, lost=True)
        _, total = lead_service.needs_attention(Lead.objects.all())
        self.assertEqual(total, 0)

    def test_unchased_sorts_above_merely_stalled(self):
        stalled = self._lead("Stalled", Lead.STAGE_APPROACH, moved_days_ago=90)
        followups.schedule(followups.LEAD, stalled, timezone.now() + timedelta(days=1))
        fresh = self._lead("Nothing booked", Lead.STAGE_NEGOTIATION, moved_days_ago=1)
        rows, _ = lead_service.needs_attention(Lead.objects.all())
        self.assertEqual(rows[0]["lead"], fresh)

    def test_the_admin_dashboard_renders_the_panel(self):
        admin = _mk_employee("ag_att_admin", role="admin")
        self._lead("Needs a call", Lead.STAGE_CONCLUSION)
        client = TestClient()
        client.force_login(admin.user)
        html = client.get(reverse("clients:admin_dashboard"), follow=True).content.decode()
        self.assertIn("Pipeline needs you", html)
        self.assertIn("Needs a call", html)
        self.assertIn("Conclusion", html, "the stage has to be on the row")

    def test_the_panel_stays_off_the_page_when_nothing_needs_chasing(self):
        admin = _mk_employee("ag_att_quiet", role="admin")
        lead = self._lead("Handled", Lead.STAGE_NEGOTIATION)
        followups.schedule(followups.LEAD, lead, timezone.now() + timedelta(days=1))
        client = TestClient()
        client.force_login(admin.user)
        html = client.get(reverse("clients:admin_dashboard"), follow=True).content.decode()
        self.assertNotIn("Pipeline needs you", html,
                         "an empty panel is noise on every dashboard load")

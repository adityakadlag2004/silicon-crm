"""A lead worked by two people, and filters that survive a stage switch.

Two things this pins:

  * The SPANCO tiles and the view tabs switch ONE control and keep the rest.
    Built from a bare `?stage=…` they reset the picked employee to the whole
    team every time somebody looked at another stage — the filter appeared to
    "jump back to all employees" on its own.
  * A lead can be shared. `assigned_to` stays the single owner; a collaborator
    sees it everywhere the owner does and is told about every move on it.
"""
from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Employee, Lead, Notification
from clients.services import leads as lead_service


class LeadFiltersSurviveStageSwitchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("share_admin", password="pw")
        cls.admin_emp = Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.other = User.objects.create_user("share_other", password="pw")
        cls.other_emp = Employee.objects.create(user=cls.other, role="employee", salary=0, active=True)
        Lead.objects.create(customer_name="Ravi", assigned_to=cls.other_emp)

    def setUp(self):
        self.c = TC()
        self.c.force_login(self.admin)

    def test_stage_tiles_keep_the_picked_employee(self):
        url = reverse("clients:lead_management")
        page = self.c.get(url, {"assigned_to": self.other_emp.pk, "q": "Ravi"})

        tiles = page.context["kpis"]
        self.assertTrue(tiles, "the SPANCO strip should render")
        for tile in tiles:
            self.assertIn(f"assigned_to={self.other_emp.pk}", tile["url"])
            self.assertIn("q=Ravi", tile["url"])
            self.assertIn("stage=", tile["url"])

    def test_view_tabs_keep_the_picked_employee(self):
        page = self.c.get(reverse("clients:lead_management"),
                          {"assigned_to": self.other_emp.pk, "stage": Lead.STAGE_SUSPECT})
        self.assertIn(f"assigned_to={self.other_emp.pk}", page.context["tab_qs"])
        self.assertIn("stage=suspect", page.context["tab_qs"])
        self.assertNotIn("view=", page.context["tab_qs"])   # the tab sets that

    def test_board_tiles_keep_the_picked_employee(self):
        page = self.c.get(reverse("clients:lead_board"), {"assigned_to": self.other_emp.pk})
        for tile in page.context["kpis"]:
            self.assertIn(f"assigned_to={self.other_emp.pk}", tile["url"])

    def test_page_number_is_dropped_so_a_new_filter_starts_at_page_one(self):
        page = self.c.get(reverse("clients:lead_management"), {"page": 2})
        for tile in page.context["kpis"]:
            self.assertNotIn("page=", tile["url"])

    def test_a_junk_employee_id_does_not_explode(self):
        page = self.c.get(reverse("clients:lead_management"), {"assigned_to": "abc"})
        self.assertEqual(page.status_code, 200)


class SharedLeadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("lead_owner", password="pw")
        cls.owner_emp = Employee.objects.create(user=cls.owner, role="employee", salary=0, active=True)
        cls.mate = User.objects.create_user("lead_mate", password="pw")
        cls.mate_emp = Employee.objects.create(user=cls.mate, role="employee", salary=0, active=True)
        cls.stranger = User.objects.create_user("lead_stranger", password="pw")
        cls.stranger_emp = Employee.objects.create(
            user=cls.stranger, role="employee", salary=0, active=True)

        cls.lead = Lead.objects.create(customer_name="Ravi", assigned_to=cls.owner_emp)
        cls.lead.collaborators.add(cls.mate_emp)

    def test_team_is_owner_first_then_collaborators(self):
        self.assertEqual(self.lead.team(), [self.owner_emp, self.mate_emp])

    def test_team_q_matches_owner_and_collaborator_but_not_a_stranger(self):
        for emp in (self.owner_emp, self.mate_emp):
            self.assertEqual(
                list(Lead.objects.filter(Lead.team_q(emp))), [self.lead], f"{emp} should see it")
        self.assertEqual(list(Lead.objects.filter(Lead.team_q(self.stranger_emp))), [])

    def test_team_q_never_doubles_a_row(self):
        """An OR across the m2m join multiplies rows; every caller counts them."""
        self.lead.collaborators.add(self.stranger_emp)
        self.assertEqual(Lead.objects.filter(Lead.team_q(self.owner_emp)).count(), 1)
        self.lead.collaborators.remove(self.stranger_emp)

    def test_collaborator_can_open_the_lead_the_stranger_cannot(self):
        detail = reverse("clients:lead_detail", args=[self.lead.pk])

        c = TC()
        c.force_login(self.mate)
        self.assertEqual(c.get(detail).status_code, 200)

        c = TC()
        c.force_login(self.stranger)
        self.assertEqual(c.get(detail).status_code, 404)

    def test_collaborator_sees_the_lead_in_my_leads(self):
        c = TC()
        c.force_login(self.mate)
        page = c.get(reverse("clients:lead_management"), {"view": "mine"})
        self.assertEqual(list(page.context["leads"]), [self.lead])
        self.assertEqual(page.context["tab_counts"]["mine"], 1)

    def test_a_stage_move_notifies_the_rest_of_the_team_not_the_actor(self):
        Notification.objects.all().delete()
        lead_service.set_stage(self.lead, Lead.STAGE_PROSPECT, user=self.owner, note="Has budget")

        rows = list(Notification.objects.all())
        self.assertEqual([n.recipient for n in rows], [self.mate])
        self.assertIn("Has budget", rows[0].body)
        self.assertIn(str(self.lead.pk), rows[0].link)

    def test_a_remark_reaches_the_other_person(self):
        Notification.objects.all().delete()
        lead_service.add_remark(self.lead, "Wants a family floater", user=self.mate)

        rows = list(Notification.objects.all())
        self.assertEqual([n.recipient for n in rows], [self.owner])
        self.assertIn("Wants a family floater", rows[0].body)

    def test_a_booked_followup_reaches_the_other_person(self):
        Notification.objects.all().delete()
        when = timezone.now() + timezone.timedelta(days=1)
        lead_service.schedule_followup(self.lead, when, note="Call back", actor=self.owner)

        # The task's own "assigned" ping goes to the owner; the team note is
        # what stops the collaborator booking the same call twice.
        self.assertTrue(
            Notification.objects.filter(recipient=self.mate,
                                        title__startswith="Follow-up booked").exists())

    def test_a_lone_lead_notifies_nobody(self):
        solo = Lead.objects.create(customer_name="Solo", assigned_to=self.owner_emp)
        Notification.objects.all().delete()
        lead_service.set_stage(solo, Lead.STAGE_PROSPECT, user=self.owner)
        self.assertEqual(Notification.objects.count(), 0)

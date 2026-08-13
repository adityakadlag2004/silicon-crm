"""The SPANCO lead pipeline: stage moves, the funnel, per-lead requirements.

Guards the two things the redesign is for — a lead's position is recorded
(never inferred from what was sold), and what a lead needs is picked per
lead instead of a fixed Health/Life/SIP grid.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Employee, Lead, LeadInterest, LeadStageEvent, Product
from clients.services import leads as lead_service


class SpancoStageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("spanco_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)

    def _lead(self, **kw):
        return Lead.objects.create(customer_name=kw.pop("name", "Ravi"), assigned_to=self.emp, **kw)

    def test_new_lead_starts_as_a_suspect(self):
        self.assertEqual(self._lead().stage, Lead.STAGE_SUSPECT)

    def test_set_stage_records_the_move(self):
        lead = self._lead()
        lead.stage_changed_at = timezone.now() - timedelta(days=9)
        lead.save(update_fields=["stage_changed_at"])

        event = lead_service.set_stage(lead, Lead.STAGE_PROSPECT, user=self.user, note="Has budget")
        lead.refresh_from_db()

        self.assertEqual(lead.stage, Lead.STAGE_PROSPECT)
        self.assertEqual(lead.days_in_stage, 0)              # the clock restarted
        self.assertEqual(event.from_stage, Lead.STAGE_SUSPECT)
        self.assertEqual(event.to_stage, Lead.STAGE_PROSPECT)
        self.assertEqual(event.note, "Has budget")
        self.assertEqual(event.created_by, self.user)

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            lead_service.set_stage(self._lead(), "closed_won")

    def test_lost_lead_keeps_the_stage_it_died_at(self):
        lead = self._lead()
        lead_service.set_stage(lead, Lead.STAGE_NEGOTIATION)
        lead_service.mark_lost(lead, user=self.user, reason="Premium too high")
        lead.refresh_from_db()

        self.assertTrue(lead.is_discarded)
        self.assertEqual(lead.stage, Lead.STAGE_NEGOTIATION)   # the weak point
        self.assertEqual(lead.lost_reason, "Premium too high")
        self.assertEqual(lead.stage_events.first().to_stage, LeadStageEvent.LOST)

    def test_reopening_clears_lost_and_logs_it(self):
        lead = self._lead()
        lead_service.set_stage(lead, Lead.STAGE_APPROACH)
        lead_service.mark_lost(lead, reason="No time")
        lead_service.reopen(lead, user=self.user)
        lead.refresh_from_db()

        self.assertFalse(lead.is_discarded)
        self.assertEqual(lead.lost_reason, "")
        self.assertEqual(lead.stage, Lead.STAGE_APPROACH)
        self.assertEqual(lead.stage_events.first().from_stage, LeadStageEvent.LOST)

    def test_stage_is_not_derived_from_sales(self):
        """The old pipeline recomputed the stage from product rows; adding a
        requirement must never move a lead by itself."""
        lead = self._lead()
        product = Product.objects.create(name="Term Plan X", code="TERMX", display_order=1)
        LeadInterest.objects.create(lead=lead, product=product, amount=50000)
        lead.refresh_from_db()
        self.assertEqual(lead.stage, Lead.STAGE_SUSPECT)


class SpancoFunnelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("funnel_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)
        # 4 suspects, 2 of which walked further; 1 lost at Negotiation.
        for i in range(4):
            Lead.objects.create(customer_name=f"S{i}", assigned_to=cls.emp)
        for stage in (Lead.STAGE_APPROACH, Lead.STAGE_ORDER):
            Lead.objects.create(customer_name=stage, assigned_to=cls.emp, stage=stage)
        Lead.objects.create(
            customer_name="Dead", assigned_to=cls.emp,
            stage=Lead.STAGE_NEGOTIATION, is_discarded=True,
        )

    def test_reached_counts_walk_backwards_through_the_funnel(self):
        rows = {r["stage"]: r for r in lead_service.funnel(Lead.objects.all())}
        self.assertEqual(rows[Lead.STAGE_SUSPECT]["reached"], 7)      # everybody
        self.assertEqual(rows[Lead.STAGE_APPROACH]["reached"], 3)     # approach + neg + order
        self.assertEqual(rows[Lead.STAGE_NEGOTIATION]["reached"], 2)
        self.assertEqual(rows[Lead.STAGE_ORDER]["reached"], 1)
        self.assertEqual(rows[Lead.STAGE_NEGOTIATION]["lost_here"], 1)
        self.assertEqual(rows[Lead.STAGE_SUSPECT]["standing"], 4)

    def test_step_conversion_is_against_the_previous_step(self):
        rows = {r["stage"]: r for r in lead_service.funnel(Lead.objects.all())}
        self.assertIsNone(rows[Lead.STAGE_SUSPECT]["step_pct"])       # nothing before it
        # 2 of the 3 that reached Approach went on to Negotiation.
        self.assertEqual(rows[Lead.STAGE_NEGOTIATION]["step_pct"], 66.7)

    def test_empty_pipeline_does_not_divide_by_zero(self):
        rows = lead_service.funnel(Lead.objects.none())
        self.assertEqual([r["reached"] for r in rows], [0] * 6)
        self.assertEqual(rows[0]["reached_pct"], 0.0)


class LeadRequirementTests(TestCase):
    """Products come from the catalog, per lead — no fixed trio anywhere."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("req_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)
        # The catalog is seeded by migration; reuse those rows.
        cls.health, _ = Product.objects.get_or_create(code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.pms, _ = Product.objects.get_or_create(code="PMS", defaults={"name": "PMS"})

    def _tc(self):
        tc = TC()
        tc.force_login(self.user)
        return tc

    def _form_payload(self, **overrides):
        payload = {
            "customer_name": "Nikhil Rao",
            "phone": "9876500000",
            "email": "",
            "assigned_to": self.emp.id,
            "stage": Lead.STAGE_PROSPECT,
            "notes": "",
            "income": "",
            "expenses": "",
            "data_received_on": "",
            "family-TOTAL_FORMS": "0", "family-INITIAL_FORMS": "0",
            "family-MIN_NUM_FORMS": "0", "family-MAX_NUM_FORMS": "1000",
            "interest-TOTAL_FORMS": "1", "interest-INITIAL_FORMS": "0",
            "interest-MIN_NUM_FORMS": "0", "interest-MAX_NUM_FORMS": "1000",
            "interest-0-product": self.pms.id,
            "interest-0-amount": "500000",
            "interest-0-note": "Wants PMS only",
        }
        payload.update(overrides)
        return payload

    def test_created_lead_carries_only_the_products_asked_for(self):
        resp = self._tc().post(reverse("clients:lead_create"), self._form_payload())
        self.assertEqual(resp.status_code, 302)

        lead = Lead.objects.get(customer_name="Nikhil Rao")
        self.assertEqual(lead.stage, Lead.STAGE_PROSPECT)
        self.assertEqual([i.product for i in lead.interests.all()], [self.pms])
        self.assertEqual(lead.interests.get().amount, 500000)
        # Entering the pipeline is itself recorded.
        self.assertEqual(lead.stage_events.get().to_stage, Lead.STAGE_PROSPECT)

    def test_a_lead_can_be_created_with_no_products_at_all(self):
        payload = self._form_payload(**{
            "customer_name": "Blank Slate",
            "interest-0-product": "", "interest-0-amount": "", "interest-0-note": "",
        })
        resp = self._tc().post(reverse("clients:lead_create"), payload)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Lead.objects.get(customer_name="Blank Slate").interests.count(), 0)

    def test_an_amount_without_a_product_is_rejected(self):
        payload = self._form_payload(**{
            "customer_name": "Loose Number",
            "interest-0-product": "", "interest-0-amount": "250000",
        })
        resp = self._tc().post(reverse("clients:lead_create"), payload)
        self.assertEqual(resp.status_code, 200)                     # redisplayed
        self.assertFalse(Lead.objects.filter(customer_name="Loose Number").exists())

    def test_stage_field_is_not_offered_on_edit(self):
        """Editing must not be a back door around the recorded stage move."""
        lead = Lead.objects.create(customer_name="Edit Me", assigned_to=self.emp)
        html = self._tc().get(reverse("clients:lead_update", args=[lead.id])).content.decode()
        self.assertNotIn('name="stage"', html)


class LeadConversionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("conv_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)

    def test_conversion_needs_the_order_stage(self):
        lead = Lead.objects.create(customer_name="Too Early", assigned_to=self.emp, stage=Lead.STAGE_CONCLUSION)
        with self.assertRaises(ValueError):
            lead_service.convert_to_client(lead, user=self.user)

    def test_conversion_creates_a_mapped_client_once(self):
        lead = Lead.objects.create(customer_name="Booked", phone="9000000001", assigned_to=self.emp)
        lead_service.set_stage(lead, Lead.STAGE_ORDER, user=self.user)
        client = lead_service.convert_to_client(lead, user=self.user)

        lead.refresh_from_db()
        self.assertEqual(lead.converted_client, client)
        self.assertEqual(client.mapped_to, self.emp)
        # Cover/SIP figures belong to approved sales, never to a lead's guesses.
        self.assertFalse(client.health_status or client.life_status or client.sip_status)

        with self.assertRaises(ValueError):
            lead_service.convert_to_client(lead, user=self.user)


class LeadScreenTests(TestCase):
    """Every pipeline screen renders — including for a plain employee."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("screen_admin", password="pw")
        cls.admin_emp = Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.staff = User.objects.create_user("screen_emp", password="pw")
        cls.staff_emp = Employee.objects.create(user=cls.staff, role="employee", salary=0, active=True)
        cls.lead = Lead.objects.create(
            customer_name="Meera Shah", phone="9000000002",
            assigned_to=cls.staff_emp, stage=Lead.STAGE_NEGOTIATION,
        )

    def _get(self, user, url):
        tc = TC()
        tc.force_login(user)
        resp = tc.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.content.decode()

    def test_screens_render_for_both_roles(self):
        for user in (self.admin, self.staff):
            for url in (
                reverse("clients:lead_management"),
                reverse("clients:lead_board"),
                reverse("clients:lead_pipeline_report"),
                reverse("clients:lead_detail", args=[self.lead.id]),
            ):
                self._get(user, url)

    def test_board_shows_the_lead_under_its_own_stage(self):
        html = self._get(self.admin, reverse("clients:lead_board"))
        for _stage, label in Lead.STAGE_CHOICES:
            self.assertIn(label, html)
        self.assertIn("Meera Shah", html)

    def test_detail_shows_the_stepper_and_the_next_step(self):
        html = self._get(self.admin, reverse("clients:lead_detail", args=[self.lead.id]))
        self.assertIn("ki-steps", html)
        self.assertIn(Lead.STAGE_HELP[Lead.STAGE_NEGOTIATION], html)
        self.assertIn('value="conclusion" selected', html)

    def test_stage_can_be_moved_from_the_screen(self):
        tc = TC()
        tc.force_login(self.staff)
        resp = tc.post(
            reverse("clients:lead_set_stage", args=[self.lead.id]),
            {"stage": Lead.STAGE_CONCLUSION, "note": "Terms agreed"},
        )
        self.assertEqual(resp.status_code, 302)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage, Lead.STAGE_CONCLUSION)
        self.assertEqual(self.lead.stage_events.first().note, "Terms agreed")

    def test_employees_cannot_touch_someone_elses_lead(self):
        outsider = User.objects.create_user("screen_out", password="pw")
        Employee.objects.create(user=outsider, role="employee", salary=0, active=True)
        tc = TC()
        tc.force_login(outsider)
        self.assertEqual(tc.get(reverse("clients:lead_detail", args=[self.lead.id])).status_code, 404)
        self.assertEqual(
            tc.post(reverse("clients:lead_set_stage", args=[self.lead.id]), {"stage": "order"}).status_code,
            404,
        )

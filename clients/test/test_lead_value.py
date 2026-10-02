"""Lead size and age: the chips, and lists that put the biggest business first.

A lead's size is the summed amounts on its interests; its age is days since
it was added. Both are derived — nothing new is stored — and every list
(web, board, app) sorts on the same `services.leads.ordered`.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Employee, Lead, LeadInterest, Product
from clients.services import leads as lead_service


class LeadValueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("value_admin", password="pw")
        cls.emp = Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)
        cls.p1 = Product.objects.create(name="Value Plan A", code="VALA", display_order=1)
        cls.p2 = Product.objects.create(name="Value Plan B", code="VALB", display_order=2)

    def _lead(self, name, *amounts, age=0, stage=Lead.STAGE_APPROACH):
        lead = Lead.objects.create(customer_name=name, assigned_to=self.emp, stage=stage)
        for product, amount in zip((self.p1, self.p2), amounts):
            LeadInterest.objects.create(lead=lead, product=product, amount=amount)
        Lead.objects.filter(pk=lead.pk).update(created_at=timezone.now() - timedelta(days=age))
        return Lead.objects.get(pk=lead.pk)

    def test_value_sums_interests_and_picks_a_tier(self):
        cases = [((400000, 200000), "premium"), ((150000,), "high"), ((25000,), "mid"),
                 ((5000,), "small"), ((None,), None), ((), None)]
        for amounts, tier in cases:
            lead = self._lead(f"Lead {amounts}", *amounts)
            got = lead.value_tier
            self.assertEqual(got and got["key"], tier, amounts)
            # The annotation and the fallback must agree.
            annotated = lead_service.with_value(Lead.objects.filter(pk=lead.pk)).get()
            self.assertEqual(annotated.total_value, lead.total_value)

    def test_age_is_since_added_not_since_the_stage_moved(self):
        for age, label, band in ((2, "2d", "fresh"), (40, "40d", "aging"), (100, "3mo", "old")):
            lead = self._lead(f"Aged {age}", age=age)
            lead_service.set_stage(lead, Lead.STAGE_NEGOTIATION)   # stage clock restarts
            self.assertEqual(lead.age_days, age)
            self.assertEqual(lead.age_label, label)
            self.assertEqual(lead.age_band[0], band)

    def test_web_list_puts_the_biggest_lead_first_and_unvalued_last(self):
        self._lead("Small Sharma", 5000)
        self._lead("Nobody Valued")
        self._lead("Big Bajaj", 600000)
        tc = TC()
        tc.force_login(self.user)
        html = tc.get(reverse("clients:lead_management")).content.decode()
        self.assertLess(html.index("Big Bajaj"), html.index("Small Sharma"))
        self.assertLess(html.index("Small Sharma"), html.index("Nobody Valued"))
        self.assertIn("★ ₹6L", html)
        self.assertIn("No amount", html)

        oldest = tc.get(reverse("clients:lead_management") + "?sort=oldest").content.decode()
        self.assertIn('value="oldest" selected', oldest)

    def test_app_list_sorts_and_carries_the_chips(self):
        self._lead("Small Sharma", 5000, age=50)
        self._lead("Big Bajaj", 600000, age=1)
        tc = TC()
        tc.force_login(self.user)
        rows = tc.get(reverse("clients:app_leads")).json()["results"]
        self.assertEqual([r["name"] for r in rows], ["Big Bajaj", "Small Sharma"])
        self.assertEqual(rows[0]["value_label"], "₹6L")
        self.assertEqual(rows[0]["value_tier"], "premium")
        self.assertEqual(rows[1]["age_band"], "aging")

        rows = tc.get(reverse("clients:app_leads") + "?sort=oldest").json()["results"]
        self.assertEqual(rows[0]["name"], "Small Sharma")
        sorts = tc.get(reverse("clients:app_lead_meta")).json()["sorts"]
        self.assertEqual(sorts[0]["value"], "value")

    def test_board_puts_the_biggest_unchased_lead_first(self):
        self._lead("Small Sharma", 5000, age=30)
        self._lead("Big Bajaj", 600000)
        page = next(p for p in lead_service.board(Lead.objects.all())
                    if p["stage"] == Lead.STAGE_APPROACH)
        self.assertEqual([r["lead"].customer_name for r in page["rows"]],
                         ["Big Bajaj", "Small Sharma"])


from io import StringIO  # noqa: E402

from django.core.management import call_command  # noqa: E402

from clients.models import LeadRemark, LeadStageEvent, Task  # noqa: E402


class _Base(TestCase):
    """An admin, a manager, a plain employee, and the catalog rows that matter."""

    @classmethod
    def setUpTestData(cls):
        def emp(name, role):
            user = User.objects.create_user(name, password="pw")
            return user, Employee.objects.create(user=user, role=role, salary=0, active=True)
        cls.admin, cls.admin_emp = emp("lv_admin", "admin")
        cls.manager, cls.manager_emp = emp("lv_manager", "manager")
        cls.staff, cls.staff_emp = emp("lv_staff", "employee")
        cls.sip, _ = Product.objects.get_or_create(code="SIP", defaults={"name": "SIP"})
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.lumpsum, _ = Product.objects.get_or_create(code="LUMSUM", defaults={"name": "Lumpsum"})

    def _lead(self, name, rows=(), stage=Lead.STAGE_NEGOTIATION, owner=None, **kw):
        lead = Lead.objects.create(customer_name=name, assigned_to=owner or self.staff_emp,
                                   stage=stage, **kw)
        for product, amount, cover in rows:
            LeadInterest.objects.create(lead=lead, product=product, amount=amount,
                                        cover_amount=cover)
        return lead

    def _tc(self, user):
        tc = TC()
        tc.force_login(user)
        return tc


class ComparableValueTests(_Base):
    def test_sip_counts_twelve_times_and_cover_never(self):
        lead = self._lead("Mixed", [(self.sip, 10000, None), (self.health, 30000, 10000000)])
        annotated = lead_service.with_value(Lead.objects.filter(pk=lead.pk)).get()
        self.assertEqual(annotated.total_value, 150000)            # 10k×12 + 30k
        self.assertEqual(Lead.objects.get(pk=lead.pk).total_value, 150000)   # fallback agrees
        self.assertEqual(annotated.value_tier["key"], "high")

    def test_size_filter_is_this_tier_and_up(self):
        big = self._lead("Big Bajaj", [(self.health, 600000, None)])
        high = self._lead("High Hegde", [(self.sip, 10000, None)])        # 1.2L a year
        small = self._lead("Small Sharma", [(self.health, 5000, None)])
        bare = self._lead("Bare Bose")
        def names(size):
            return set(lead_service.filter_size(Lead.objects.all(), size)
                       .values_list("customer_name", flat=True))
        self.assertEqual(names("premium"), {big.customer_name})
        self.assertEqual(names("high"), {big.customer_name, high.customer_name})
        self.assertEqual(names("none"), {bare.customer_name})
        self.assertEqual(len(names("bogus")), 4)
        self.assertIn(small.customer_name, names("bogus"))

        html = self._tc(self.admin).get(reverse("clients:lead_management") + "?size=premium").content.decode()
        self.assertIn("Big Bajaj", html)
        self.assertNotIn("Small Sharma", html)
        rows = self._tc(self.admin).get(reverse("clients:app_leads") + "?size=high").json()["results"]
        self.assertEqual({r["name"] for r in rows}, {"Big Bajaj", "High Hegde"})

    def test_stage_values_and_forecast(self):
        self._lead("Neg", [(self.health, 200000, None)], stage=Lead.STAGE_NEGOTIATION)
        self._lead("Won", [(self.health, 900000, None)], stage=Lead.STAGE_ORDER)
        v = lead_service.stage_values(Lead.objects.filter(is_discarded=False))
        self.assertEqual(v[Lead.STAGE_NEGOTIATION]["value"], 200000)
        self.assertEqual(v[Lead.STAGE_NEGOTIATION]["forecast"], 100000)     # × 50%
        # Booked business is not pipeline still to win.
        self.assertEqual(v["pipeline"], {"value": 200000, "forecast": 100000})

        html = self._tc(self.admin).get(reverse("clients:lead_pipeline_report")).content.decode()
        self.assertIn("Forecast", html)
        self.assertIn("₹1L</strong> expected", html)
        board = self._tc(self.admin).get(reverse("clients:lead_board")).content.decode()
        self.assertIn("exp ₹1L", board)
        pipe = self._tc(self.admin).get(reverse("clients:app_dashboard")).json()["pipeline"]
        self.assertEqual(pipe["forecast"], 100000)

    def test_app_interest_writes_only_what_it_was_sent(self):
        lead = self._lead("Edit", [(self.health, 30000, None)])
        tc = self._tc(self.staff)
        url = reverse("clients:app_lead_interest", args=[lead.id])
        tc.post(url, {"product_id": self.health.id}, content_type="application/json")
        self.assertEqual(lead.interests.get().amount, 30000)        # not wiped
        tc.post(url, {"product_id": self.health.id, "cover_amount": "1000000"},
                content_type="application/json")
        row = lead.interests.get()
        self.assertEqual((row.amount, row.cover_amount), (30000, 1000000))
        meta = tc.get(reverse("clients:app_lead_meta")).json()
        sip = next(p for p in meta["products"] if p["id"] == self.sip.id)
        self.assertTrue(sip["monthly"])
        self.assertEqual(meta["sizes"][0]["value"], "premium")

    def test_legacy_wealth_targets_move_to_lumpsum(self):
        lead = self._lead("Legacy")
        row = LeadInterest.objects.create(lead=lead, product=self.sip, amount=10000000,
                                          note="Wealth (pre-SPANCO), target 10000000, Pending")
        monthly = LeadInterest.objects.create(lead=self._lead("Monthly"), product=self.sip,
                                              amount=5000, note="Wealth (pre-SPANCO), target 5000")
        call_command("fix_lead_wealth_targets", stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.product, self.sip)                     # dry run wrote nothing
        call_command("fix_lead_wealth_targets", "--apply", stdout=StringIO())
        row.refresh_from_db()
        monthly.refresh_from_db()
        self.assertEqual(row.product, self.lumpsum)
        self.assertEqual(monthly.product, self.sip)                 # a real monthly SIP stays


class PremiumAlertTests(_Base):
    def _quiet(self, name, amount=600000, days=10):
        lead = self._lead(name, [(self.health, amount, None)])
        Lead.objects.filter(pk=lead.pk).update(
            stage_changed_at=timezone.now() - timedelta(days=days))
        return Lead.objects.get(pk=lead.pk)

    def test_quiet_premium_lead_rings_its_owner_once(self):
        lead = self._quiet("Quiet Big")
        self._quiet("Quiet Small", amount=50000)                    # not premium
        self._quiet("Recent", days=3)                               # moved this week
        LeadRemark.objects.create(lead=self._quiet("Remarked"), text="Spoke today")

        call_command("premium_lead_alerts", stdout=StringIO())
        tasks = Task.objects.filter(source_kind="lead")
        self.assertEqual(list(tasks.values_list("source_id", flat=True)), [lead.pk])
        task = tasks.get()
        self.assertEqual(task.priority, Task.PRIORITY_HIGH)
        self.assertEqual(task.assigned_to, self.staff_emp)
        self.assertIsNotNone(task.due_time)                         # or it never rings

        call_command("premium_lead_alerts", stdout=StringIO())      # the open task is the dedup
        self.assertEqual(Task.objects.filter(source_kind="lead").count(), 1)


class LossSignoffTests(_Base):
    def _premium(self):
        return self._lead("Big Fish", [(self.health, 700000, None)])

    def test_employee_cannot_drop_a_premium_lead_alone(self):
        lead = self._premium()
        event = lead_service.mark_lost(lead, user=self.staff, reason="Went to an agent")
        lead.refresh_from_db()
        self.assertFalse(lead.is_discarded)
        self.assertEqual(event.to_stage, LeadStageEvent.LOSS_REQUESTED)
        self.assertEqual(lead.loss_requested_by, self.staff)
        signoff = Task.objects.filter(source_id=lead.pk, assign_group__startswith="lossreq:")
        self.assertEqual({t.assigned_to for t in signoff}, {self.admin_emp, self.manager_emp})
        self.assertIsNone(lead_service.mark_lost(lead, user=self.staff))   # replay = no-op
        self.assertEqual(signoff.count(), 2)

        lead_service.mark_lost(lead, user=self.manager, reason="Went to an agent")
        lead.refresh_from_db()
        self.assertTrue(lead.is_discarded)
        self.assertIsNone(lead.loss_requested_at)
        self.assertFalse(signoff.filter(status__in=Task.OPEN_STATUSES).exists())

    def test_small_lead_and_the_system_drop_straight_away(self):
        small = self._lead("Minnow", [(self.health, 20000, None)])
        lead_service.mark_lost(small, user=self.staff)
        self.assertTrue(Lead.objects.get(pk=small.pk).is_discarded)
        big = self._premium()
        lead_service.mark_lost(big)                                  # seeds / commands
        self.assertTrue(Lead.objects.get(pk=big.pk).is_discarded)

    def test_decline_and_stage_move_both_clear_the_request(self):
        lead = self._premium()
        lead_service.mark_lost(lead, user=self.staff)
        lead_service.decline_loss(lead, self.admin, "Call them once more")
        lead.refresh_from_db()
        self.assertIsNone(lead.loss_requested_at)
        self.assertFalse(lead.is_discarded)

        lead_service.mark_lost(lead, user=self.staff)
        lead_service.set_stage(lead, Lead.STAGE_CONCLUSION, user=self.staff)
        lead.refresh_from_db()
        self.assertIsNone(lead.loss_requested_at)
        self.assertFalse(Task.objects.filter(source_id=lead.pk, assign_group__startswith="lossreq:",
                                             status__in=Task.OPEN_STATUSES).exists())

    def test_both_doors(self):
        lead = self._premium()
        resp = self._tc(self.staff).post(
            reverse("clients:app_lead_action", args=[lead.id]),
            {"action": "discard", "reason": "No budget"}, content_type="application/json").json()
        self.assertTrue(resp["requested"])
        self.assertEqual(self._tc(self.staff).post(
            reverse("clients:app_lead_action", args=[lead.id]),
            {"action": "decline_loss"}, content_type="application/json").status_code, 403)

        html = self._tc(self.admin).get(reverse("clients:lead_detail", args=[lead.id])).content.decode()
        self.assertIn("asked to drop this Premium lead", html)
        self.assertIn("Keep working it", html)
        self.assertIn("Drop requested", html)
        detail = self._tc(self.admin).get(reverse("clients:app_lead_detail", args=[lead.id])).json()
        self.assertEqual(detail["loss_request"]["reason"], "No budget")
        self.assertTrue(detail["can_decide_loss"])

        self._tc(self.admin).post(reverse("clients:lead_decline_loss", args=[lead.id]))
        self.assertIsNone(Lead.objects.get(pk=lead.pk).loss_requested_at)
        self.assertEqual(self._tc(self.staff).post(
            reverse("clients:lead_decline_loss", args=[lead.id])).status_code, 403)

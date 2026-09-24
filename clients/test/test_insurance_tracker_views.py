"""The tracker reads one product line at a time; the profile shows the book.

Three complaints these pin: the tracker was one undivided list, the client
profile never named a policy, and a renewal carried no record of whether its
document had been filed.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (Client, Employee, InsurancePolicy, Product,
                            Renewal, Sale)


class TrackerTypeTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("trk_admin", password="x")
        cls.emp = Employee.objects.create(
            user=cls.user, role="admin", salary=0, active=True)
        cls.customer = Client.objects.create(name="TRK Client")
        today = timezone.localdate()
        cls.health = InsurancePolicy.objects.create(
            client=cls.customer, policy_number="HEALTHONE", insurer="Starwell Health Co",
            insurance_type=InsurancePolicy.TYPE_HEALTH,
            sum_insured=Decimal("500000"), premium_amount=Decimal("18000"),
            start_date=today, end_date=today + datetime.timedelta(days=10))
        cls.life = InsurancePolicy.objects.create(
            client=cls.customer, policy_number="LIFEONE", insurer="Lifecorp Assurance",
            insurance_type=InsurancePolicy.TYPE_LIFE,
            sum_insured=Decimal("1000000"), premium_amount=Decimal("40000"),
            start_date=today, end_date=today + datetime.timedelta(days=400))

    def _get(self, url):
        self.client.force_login(self.user)
        return self.client.get(url).content.decode()

    def test_all_tab_shows_both_books(self):
        html = self._get(reverse("clients:policy_list"))
        self.assertIn("Starwell", html)
        self.assertIn("Lifecorp", html)

    def test_health_tab_excludes_life(self):
        html = self._get(reverse("clients:policy_list") + "?type=health")
        self.assertIn("Starwell", html)
        self.assertNotIn("Lifecorp", html)

    def test_life_tab_excludes_health(self):
        html = self._get(reverse("clients:policy_list") + "?type=life")
        self.assertIn("Lifecorp", html)
        self.assertNotIn("Starwell", html)

    def test_kpis_are_scoped_to_the_selected_type(self):
        """The strip must describe the book on screen, not the whole tracker —
        a Health tab reporting Life's cover is worse than no number."""
        resp = self.client.force_login(self.user) or self.client.get(
            reverse("clients:policy_list") + "?type=health")
        kpis = {k["label"]: k["value"] for k in resp.context["kpis"]}
        self.assertEqual(kpis["Policies"], 1)
        self.assertIn("5,00,000", kpis["Active Cover"])

    def test_type_survives_a_kpi_tile_click(self):
        resp = self.client.force_login(self.user) or self.client.get(
            reverse("clients:policy_list") + "?type=health")
        for k in resp.context["kpis"]:
            if k.get("url"):
                self.assertIn("type=health", k["url"])

    def test_soonest_renewal_comes_first(self):
        resp = self.client.force_login(self.user) or self.client.get(
            reverse("clients:policy_list"))
        rows = list(resp.context["policies"])
        self.assertEqual(rows[0], self.health)   # 10 days out, not 400

    def test_a_lapsed_policy_never_heads_the_queue(self):
        """Its end_date is in the past, so a plain date sort floats every dead
        policy above the one that needs a call this week."""
        dead = InsurancePolicy.objects.create(
            client=self.customer, policy_number="DEADONE", insurer="Gone Ltd",
            insurance_type=InsurancePolicy.TYPE_HEALTH,
            status=InsurancePolicy.STATUS_LAPSED,
            end_date=timezone.localdate() - datetime.timedelta(days=40))
        resp = self.client.force_login(self.user) or self.client.get(
            reverse("clients:policy_list"))
        rows = list(resp.context["policies"])
        self.assertEqual(rows[0], self.health)
        self.assertEqual(rows[-1], dead)

    def test_tab_counts_are_reported(self):
        resp = self.client.force_login(self.user) or self.client.get(
            reverse("clients:policy_list"))
        counts = {t["label"]: t["count"] for t in resp.context["tabs"]}
        self.assertEqual(counts["All"], 2)
        self.assertEqual(counts["Health"], 1)
        self.assertEqual(counts["Life"], 1)

    def test_tab_counts_survive_policies_with_renewals(self):
        """The list queryset carries a Count("renewals") join; grouping over
        it counts joined rows, so a policy with three renewals was landing in
        its tab three times and the tabs stopped summing to the total."""
        product, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        for n in range(3):
            Renewal.objects.create(
                client=self.customer, policy=self.health, product_ref=product,
                product_type=Renewal.PRODUCT_TYPE_HEALTH,
                renewal_date=datetime.date(2026, 4, 1) + datetime.timedelta(days=n),
                frequency=Renewal.FREQUENCY_YEARLY, premium_amount=Decimal("100"))
        resp = self.client.force_login(self.user) or self.client.get(
            reverse("clients:policy_list"))
        counts = {t["label"]: t["count"] for t in resp.context["tabs"]}
        self.assertEqual(counts["Health"], 1)
        self.assertEqual(counts["All"], 2)
        self.assertEqual(counts["All"], counts["Health"] + counts["Life"])


class ClientProfileShowsTheBookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("prof_admin", password="x")
        Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)
        cls.customer = Client.objects.create(name="PROF Client")
        cls.policy = InsurancePolicy.objects.create(
            client=cls.customer, policy_number="PROFPOL1", insurer="Niva Bupa",
            insurance_type=InsurancePolicy.TYPE_HEALTH,
            sum_insured=Decimal("700000"), premium_amount=Decimal("21000"),
            start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 4, 1))
        cls.health_product, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        Renewal.objects.create(
            client=cls.customer, policy=cls.policy, product_ref=cls.health_product,
            product_type=Renewal.PRODUCT_TYPE_HEALTH,
            renewal_date=datetime.date(2026, 4, 1),
            frequency=Renewal.FREQUENCY_YEARLY,
            premium_amount=Decimal("21000"), policy_doc_submitted=True)

    def _get(self):
        self.client.force_login(self.user)
        return self.client.get(
            reverse("clients:client_profile", args=[self.customer.id]))

    def test_profile_names_the_policy(self):
        html = self._get().content.decode()
        self.assertIn("Health &amp; Life Policies", html)
        self.assertIn("PROFPOL1", html)          # full number on the record page
        self.assertIn("Niva Bupa", html)

    def test_health_and_life_has_its_own_tab_next_to_overview(self):
        html = self._get().content.decode()
        self.assertIn('data-tab="insurance"', html)      # the button
        self.assertIn('data-panel="insurance"', html)    # the panel it reveals
        # the tab sits immediately right of Overview in the strip
        self.assertLess(html.index('data-tab="overview"'),
                        html.index('data-tab="insurance"'))
        self.assertLess(html.index('data-tab="insurance"'),
                        html.index('data-tab="portfolio"'))

    def test_the_tab_strip_is_not_hidden_by_its_own_switcher(self):
        """The buttons must not carry data-panel: the switcher hides every
        [data-panel] that is not active, so sharing the attribute made the
        page paint all five tabs and then hide four of them."""
        html = self._get().content.decode()
        strip = html[html.index('id="profileTabs"'):]
        strip = strip[:strip.index("</div>")]
        self.assertNotIn("data-panel", strip)
        self.assertEqual(strip.count("data-tab="), 6)   # + External

    def test_the_tab_lists_only_health_and_life(self):
        InsurancePolicy.objects.create(
            client=self.customer, policy_number="MOTOR1",
            insurance_type=InsurancePolicy.TYPE_MOTOR)
        rows = self._get().context["insurance_policies"]
        self.assertEqual([p.policy_number for p in rows], ["PROFPOL1"])

    def test_the_tab_shows_the_renewals_against_a_policy(self):
        html = self._get().content.decode()
        self.assertIn("Renewals collected (1)", html)

    def test_the_tab_reads_stored_policies_not_a_rescan(self):
        """The point of the tab: it renders the tracker's own rows, so its cost
        does not grow with the client's sales and renewal history."""
        rows = self._get().context["insurance_policies"]
        self.assertEqual(rows[0].renewal_count, 1)
        # renewals came from the prefetch, so touching them costs no query
        with self.assertNumQueries(0):
            list(rows[0].renewals.all())

    def test_profile_carries_the_policy_in_context(self):
        self.assertEqual(list(self._get().context["policies"]), [self.policy])

    def test_health_cover_tile_reads_the_sales_book(self):
        """The tiles are per product line now, derived from sales/renewals."""
        kpis = {k["label"]: k["value"] for k in self._get().context["kpis"]}
        self.assertIn("Health Cover", kpis)
        self.assertIn("Life Cover", kpis)

    def test_renewal_count_is_shown_per_policy(self):
        self.assertEqual(self._get().context["policies"][0].renewal_count, 1)

    def test_filing_state_shows_on_the_renewal_row(self):
        self.assertIn("Filed", self._get().content.decode())


class RenewalFilingChecklistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.health.domain = Product.DOMAIN_BOTH
        cls.health.save()
        cls.user = User.objects.create_user("chk_emp", password="x")
        cls.emp = Employee.objects.create(
            user=cls.user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="CHK Client", mapped_to=cls.emp)

    def _post(self, **extra):
        data = {
            "client": self.customer.id, "product_ref": self.health.id,
            "renewal_date": "2026-05-10", "frequency": Renewal.FREQUENCY_YEARLY,
            "premium_amount": "15000", "premium_collected_on": "2026-05-10",
            "employee": self.emp.id, "policy_number": "CHK900", "insurer": "Star Health",
        }
        data.update(extra)
        self.client.force_login(self.user)
        return self.client.post(reverse("clients:add_renewal"), data)

    def test_unticked_means_not_filed(self):
        """Blank must stay False — the whole point of the tick is that a
        missing document is visible."""
        self._post()
        self.assertFalse(Renewal.objects.latest("id").policy_doc_submitted)

    def test_ticked_is_recorded(self):
        self._post(policy_doc_submitted="on")
        self.assertTrue(Renewal.objects.latest("id").policy_doc_submitted)

    def test_the_checklist_renders_on_the_form(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("clients:add_renewal")).content.decode()
        self.assertIn("Renewal policy submitted to Google Drive", html)

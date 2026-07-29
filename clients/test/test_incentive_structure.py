"""The Life FY bonus ladder and the Health monthly rate bands.

Pins the two things that are easy to break and expensive to get wrong: the
earned-to-date delta (a rung must never pay twice) and the band a seller's own
monthly volume resolves to.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from clients.models import Client, Employee, IncentiveRule, Product, Sale
from clients.services import incentives as inc


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.life, _ = Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance"})
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        call_command("seed_incentive_structure")
        cls.user = User.objects.create_user("seller", password="x")
        cls.emp = Employee.objects.create(user=cls.user, role="employee")
        cls.client_rec = Client.objects.create(name="A Client")

    def sell(self, product, amount, on, **kw):
        return Sale.objects.create(
            client=self.client_rec, employee=self.emp, product=product.name,
            product_ref=product, amount=Decimal(str(amount)), date=on,
            status=Sale.STATUS_APPROVED, **kw,
        )


class LifeLadderTests(_Base):
    def test_base_pays_on_every_policy_with_no_minimum(self):
        s = self.sell(self.life, 80000, date(2026, 6, 10))
        # 1.75% of 80,000 — under the old plan a lone 80k month paid nothing.
        self.assertEqual(s.points, Decimal("1400.000"))
        self.assertEqual(s.bonus_points, Decimal("0.000"))

    def test_rung_releases_only_the_difference(self):
        self.sell(self.life, 150000, date(2026, 6, 1))
        self.sell(self.life, 100000, date(2026, 6, 20))
        crossing = self.sell(self.life, 60000, date(2026, 7, 5))  # cumulative 3,10,000
        self.assertEqual(crossing.bonus_points, Decimal("3000.000"))
        self.assertEqual(crossing.points, Decimal("4050.000"))  # 1.75% of 60k + 3,000

        # Next rung is 9L → 7,500 to date, and 3,000 is already out.
        nxt = self.sell(self.life, 600000, date(2026, 8, 3))
        self.assertEqual(nxt.bonus_points, Decimal("4500.000"))

    def test_a_rung_never_pays_twice(self):
        self.sell(self.life, 400000, date(2026, 5, 5))
        again = self.sell(self.life, 100000, date(2026, 5, 25))
        self.assertEqual(again.bonus_points, Decimal("0.000"))
        self.assertEqual(again.points, Decimal("1750.000"))

    def test_ladder_accumulates_across_months_and_resets_on_1_april(self):
        # Three sub-3L months inside one FY still reach the rung; under the old
        # monthly slab every one of them paid zero bonus.
        self.sell(self.life, 120000, date(2026, 6, 1))
        self.sell(self.life, 120000, date(2026, 7, 1))
        last = self.sell(self.life, 120000, date(2026, 8, 1))
        self.assertEqual(last.bonus_points, Decimal("3000.000"))

        # A new FY starts clean: March is FY2025, April is FY2026.
        march = self.sell(self.life, 100000, date(2026, 3, 30))
        self.assertEqual(march.bonus_points, Decimal("0.000"))

    def test_rejected_sale_earns_nothing_and_leaves_the_running_total(self):
        self.sell(self.life, 290000, date(2026, 6, 1))
        bad = self.sell(self.life, 500000, date(2026, 6, 5))
        bad.status = Sale.STATUS_REJECTED
        bad.save()
        self.assertEqual(bad.points, Decimal("0.000"))
        after = self.sell(self.life, 5000, date(2026, 6, 9))  # 2,95,000 — short of 3L
        self.assertEqual(after.bonus_points, Decimal("0.000"))


class HealthBandTests(_Base):
    def test_band_comes_from_the_sellers_own_monthly_volume(self):
        first = self.sell(self.health, 20000, date(2026, 6, 2), policy_type="fresh")
        self.assertEqual(first.points, Decimal("300.000"))  # 1.50% band

        # Crossing 25,000 re-rates the whole month at 2.00%.
        second = self.sell(self.health, 10000, date(2026, 6, 12), policy_type="fresh")
        self.assertEqual(second.points, Decimal("200.000"))
        first.refresh_from_db()
        self.assertEqual(first.points, Decimal("300.000"))  # only resyncs on review

    def test_every_band(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        cases = [("0", "1.50"), ("24999", "1.50"), ("25000", "2.00"),
                 ("49999", "2.00"), ("50000", "2.25"), ("99999", "2.25"),
                 ("100000", "2.75"), ("199999", "2.75"), ("200000", "3.00"),
                 ("299999", "3.00"), ("300000", "3.50"), ("900000", "3.50")]
        # The sale itself counts toward the volume that picks the band, so the
        # cases below are the cumulative total *including* this ₹1,000 sale.
        for volume, rate in cases:
            q = inc.quote(rule, Decimal("1000"),
                          prior_volume=Decimal(volume) - Decimal("1000"),
                          policy_type="fresh", is_health=True)
            self.assertEqual(q["rate"], Decimal(rate), msg=f"volume {volume}")

    def test_port_pays_its_own_flat_rate_and_stays_out_of_the_ladder(self):
        port = self.sell(self.health, 400000, date(2026, 6, 2), policy_type="port")
        self.assertEqual(port.points, Decimal("2680.000"))  # 0.67% of 4,00,000

        # That 4L must not push a small Fresh sale into the top band.
        fresh = self.sell(self.health, 10000, date(2026, 6, 5), policy_type="fresh")
        self.assertEqual(fresh.points, Decimal("150.000"))  # still the 1.50% band

    def test_multiyear_credits_one_year_at_a_time(self):
        s = self.sell(self.health, 90000, date(2026, 6, 2),
                      policy_type="fresh", policy_years=3)
        # 30,000 counted now → 2.00% band → 600, not 3 years' worth.
        self.assertEqual(s.points, Decimal("600.000"))

    def test_health_ladder_resets_each_month(self):
        self.sell(self.health, 250000, date(2026, 6, 2), policy_type="fresh")
        july = self.sell(self.health, 10000, date(2026, 7, 2), policy_type="fresh")
        self.assertEqual(july.points, Decimal("150.000"))  # 1.50%, not 3.00%


class StructurePageTests(_Base):
    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

    def test_page_lists_the_ladders(self):
        r = self.client.get(reverse("clients:incentive_structure"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Life Insurance")
        self.assertContains(r, "Health Insurance")

    def test_calculator_agrees_with_what_a_real_sale_pays(self):
        rule = IncentiveRule.objects.get(product_ref=self.life)
        r = self.client.get(reverse("clients:incentive_structure"), {
            "rule": rule.id, "amount": "60000", "prior_volume": "250000",
            "policy_type": "fresh", "policy_years": "1",
        })
        self.assertEqual(r.status_code, 200)

        self.sell(self.life, 250000, date(2026, 6, 1))
        real = self.sell(self.life, 60000, date(2026, 6, 20))
        self.assertEqual(r.context["trial"]["result"]["total"], real.points)

    def test_calculator_survives_junk_input(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.get(reverse("clients:incentive_structure"),
                            {"rule": rule.id, "amount": "not a number",
                             "prior_volume": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["trial"]["result"]["total"], Decimal("0"))


class CalculatorTests(_Base):
    """The employee-facing what-if tool."""

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("clients:incentive_calculator")
        # The tool always models the period we are in right now, so the fixture
        # sales have to land in it — a hard-coded month silently empties out.
        self.today = timezone.localdate()

    def _rows(self, **kw):
        base = {"rule_0": "", "amount_0": "", "ptype_0": "fresh", "years_0": "1"}
        base.update(kw)
        return base

    def test_any_employee_can_open_it(self):
        # No manage_incentives permission — this is the seller's own pay.
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_starts_from_what_is_already_banked(self):
        self.sell(self.life, 250000, self.today)
        r = self.client.get(self.url)
        line = next(l for l in r.context["lines"] if l["rule"].product == "Life Insurance")
        self.assertEqual(line["volume"], Decimal("250000"))
        self.assertEqual(line["booked"], Decimal("4375.000"))  # 1.75%

    def test_assumption_releases_the_rung_the_banked_volume_is_short_of(self):
        self.sell(self.life, 250000, self.today)
        rule = IncentiveRule.objects.get(product_ref=self.life)
        r = self.client.get(self.url, self._rows(rule_0=str(rule.id), amount_0="60000"))
        line = next(l for l in r.context["lines"] if l["rule"].product == "Life Insurance")
        # 1.75% of 60,000 + the 3L rung, which 2.5L alone never reached.
        self.assertEqual(line["added"], Decimal("4050.00"))

    def test_a_rung_already_banked_is_not_offered_twice(self):
        self.sell(self.life, 400000, self.today)
        rule = IncentiveRule.objects.get(product_ref=self.life)
        r = self.client.get(self.url, self._rows(rule_0=str(rule.id), amount_0="50000"))
        line = next(l for l in r.context["lines"] if l["rule"].product == "Life Insurance")
        self.assertEqual(line["added"], Decimal("875.00"))  # base only

    def test_health_reprices_the_whole_month_when_a_band_is_crossed(self):
        self.sell(self.health, 20000, self.today, policy_type="fresh")
        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.get(self.url, self._rows(rule_0=str(rule.id), amount_0="10000"))
        line = next(l for l in r.context["lines"] if l["rule"].product == "Health Insurance")
        # 30,000 total at 2.00% = 600, against 20,000 at 1.50% = 300 already.
        self.assertEqual(line["added"], Decimal("300.00"))
        self.assertEqual(line["rate"], Decimal("2.00"))

    def test_next_rung_names_the_gap_and_what_it_is_worth(self):
        self.sell(self.life, 250000, self.today)
        r = self.client.get(self.url)
        line = next(l for l in r.context["lines"] if l["rule"].product == "Life Insurance")
        self.assertEqual(line["next"]["gap"], Decimal("50000"))
        self.assertEqual(line["next"]["worth"], Decimal("3000"))

    def test_multiyear_health_counts_one_year(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.get(self.url, self._rows(
            rule_0=str(rule.id), amount_0="90000", years_0="3"))
        line = next(l for l in r.context["lines"] if l["rule"].product == "Health Insurance")
        self.assertEqual(line["assumed"], Decimal("30000"))

    def test_port_pays_its_own_rate_without_lifting_the_band(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.get(self.url, self._rows(
            rule_0=str(rule.id), amount_0="400000", ptype_0="port"))
        line = next(l for l in r.context["lines"] if l["rule"].product == "Health Insurance")
        self.assertEqual(line["added"], Decimal("2680.00"))
        self.assertEqual(line["final_volume"], Decimal("0"))  # Fresh ladder untouched

    def test_an_employee_cannot_model_someone_else(self):
        other_user = User.objects.create_user("rival", password="x")
        other = Employee.objects.create(user=other_user, role="employee")
        Sale.objects.create(client=self.client_rec, employee=other,
                            product="Life Insurance", product_ref=self.life,
                            amount=Decimal("900000"), date=self.today,
                            status=Sale.STATUS_APPROVED)
        r = self.client.get(self.url, {"employee": other.id})
        self.assertEqual(r.context["target"], self.emp)
        self.assertEqual(r.context["banked_total"], Decimal("0"))

    def test_an_admin_can_model_someone_else(self):
        boss = User.objects.create_user("boss", password="x")
        Employee.objects.create(user=boss, role="admin")
        self.sell(self.life, 900000, self.today)
        self.client.force_login(boss)
        r = self.client.get(self.url, {"employee": self.emp.id})
        self.assertEqual(r.context["target"], self.emp)
        self.assertGreater(r.context["banked_total"], Decimal("0"))

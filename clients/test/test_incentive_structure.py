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


class NoMonthlyDeductionTests(_Base):
    """The prize is decided by the financial year's life volume and nothing else.

    Until 2026-07-31 a month that crossed ₹3,00,000 had the old monthly grid
    (8,000 per step) subtracted from the yearly prize, which cancelled it
    outright and made a good month worth less than the same business spread
    thinly. That deduction was removed from the system.
    """

    def sell_life(self, amount, on):
        """Book through the service — the path every real entry uses."""
        from clients.services import sales as sales_service

        s = Sale(client=self.client_rec, employee=self.emp, product=self.life.name,
                 product_ref=self.life, amount=Decimal(str(amount)), date=on)
        return sales_service.finalize_new_sale(s, self.user, auto_approve=True)

    def test_a_month_over_three_lakh_pays_the_rung_in_full(self):
        s = self.sell_life(310000, date(2026, 8, 10))
        self.assertEqual(s.bonus_points, Decimal("3000.000"))
        self.assertEqual(s.points, Decimal("8425.000"))   # 1.75% of 3.1L + 3,000

    def test_the_same_year_pays_the_same_however_the_months_fall(self):
        """₹9L in one month, or in three, or in nine — one ₹7,500 prize."""
        splits = {
            "one month": [(date(2026, 8, 10), 900000)],
            "three months": [(date(2026, m, 10), 300000) for m in (8, 9, 10)],
            "nine months": [(date(2026, m, 10), 100000) for m in (5, 6, 7, 8, 9, 10, 11, 12)]
                            + [(date(2027, 1, 10), 100000)],
        }
        totals = {}
        for label, sales in splits.items():
            Sale.objects.filter(employee=self.emp).delete()
            for on, amount in sales:
                self.sell_life(amount, on)
            st = inc.life_bonus_status(self.rule_life(), self.emp, 2026)
            totals[label] = (st["volume"], st["level"], st["released"])
        self.assertEqual(len(set(totals.values())), 1, totals)
        self.assertEqual(list(totals.values())[0][1], Decimal("7500"))

    def test_the_rule_carries_no_deduction_switch_any_more(self):
        rule = self.rule_life()
        self.assertFalse(hasattr(rule, "deduct_legacy_monthly"))
        self.assertFalse(hasattr(inc, "legacy_monthly_payout"))
        self.assertFalse(hasattr(inc, "LEGACY_MONTHLY_SLAB"))

    def rule_life(self):
        return IncentiveRule.objects.get(product_ref=self.life)


class HealthBandTests(_Base):
    def test_band_comes_from_the_sellers_own_monthly_volume(self):
        first = self.sell(self.health, 20000, date(2026, 6, 2), policy_type="fresh")
        self.assertEqual(first.points, Decimal("300.000"))  # 1.50% band

        # Crossing 25,000 puts this sale in the 2.00% band.
        second = self.sell(self.health, 10000, date(2026, 6, 12), policy_type="fresh")
        self.assertEqual(second.points, Decimal("200.000"))
        # These fixtures write Sale.objects.create() directly, which prices only
        # the row being saved. Repricing the rest of the month is the service
        # layer's job — see BandCrossingTests for the paths the app actually uses.
        first.refresh_from_db()
        self.assertEqual(first.points, Decimal("300.000"))

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

    def test_port_earns_nothing_and_stays_out_of_the_ladder(self):
        port = self.sell(self.health, 400000, date(2026, 6, 2), policy_type="port")
        self.assertEqual(port.points, Decimal("0.000"))

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


class PortEarnsNothingTests(_Base):
    """Port pays 0%, everywhere, and the Fresh band never sees it.

    The dangerous failure here is silent: delete the Port branch in quote()
    and a Port sale falls through to the Fresh band, paying the month's rate
    instead of nothing.
    """

    def test_a_port_sale_earns_zero_whatever_the_month_has_reached(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        # A month already in the top band — the branch has to hold there too.
        self.sell(self.health, 350000, date(2026, 6, 1), policy_type="fresh")
        port = self.sell(self.health, 200000, date(2026, 6, 5), policy_type="port")
        self.assertEqual(port.points, Decimal("0.000"))
        q = inc.quote(rule, Decimal("200000"), prior_volume=Decimal("350000"),
                      policy_type="port", is_health=True)
        self.assertEqual(q["total"], Decimal("0"))
        self.assertEqual(q["rate"], Decimal("0"))

    def test_port_volume_never_lifts_the_fresh_band(self):
        self.sell(self.health, 400000, date(2026, 7, 2), policy_type="port")
        fresh = self.sell(self.health, 10000, date(2026, 7, 5), policy_type="fresh")
        self.assertEqual(fresh.points, Decimal("150.000"))   # still the 1.50% band

    def test_the_rule_carries_no_port_rate_any_more(self):
        self.assertFalse(hasattr(IncentiveRule.objects.get(product_ref=self.health),
                                 "port_percent"))

    def test_the_explainer_says_port_earns_nothing(self):
        e = inc.explain(IncentiveRule.objects.get(product_ref=self.health), is_health=True)
        blob = " ".join(e["notes"]).lower()
        self.assertIn("port policy earns no points", blob)


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


class RuleScreenReadsPlainlyTests(_Base):
    """The rules screen must say what a rule does, not leave it to be worked
    out from `unit_amount` and `points_per_unit`.

    It also used to print a rate band's payout with floatformat:0, so Health's
    2.25% band rendered as "2 pts" — the wrong number and the wrong unit.
    """

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.url = reverse("clients:manage_incentive_rules")

    def test_each_rule_carries_a_plain_sentence_and_its_base_percent(self):
        r = self.client.get(self.url)
        rules = {x.product: x for x in r.context["rules"]}
        life = rules["Life Insurance"]
        self.assertEqual(life.base_percent, Decimal("1.7500"))
        self.assertIn("prize", life.summary.lower())
        self.assertIn("April–March", life.summary)
        health = rules["Health Insurance"]
        self.assertIn("rate", health.summary.lower())
        self.assertIn("month", health.summary)

    def test_a_rate_band_is_shown_as_a_percentage_not_as_points(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("2.25%", html)            # the band's real value
        self.assertNotIn("2.25 pts", html)
        self.assertIn("1.75% of the sale", html)  # base, stated once, plainly

    def test_the_screen_never_mentions_a_deduction(self):
        # Bare "legacy" is not checked: base.html carries it in an unrelated
        # script comment, and this assertion is about what the page says.
        html = self.client.get(self.url).content.decode().lower()
        for word in ("deduct", "legacy slab", "monthly bonus", "netted"):
            self.assertNotIn(word, html, msg=f"rule screen still mentions {word!r}")


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

    def test_port_earns_nothing_and_does_not_lift_the_band(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.get(self.url, self._rows(
            rule_0=str(rule.id), amount_0="400000", ptype_0="port"))
        line = next(l for l in r.context["lines"] if l["rule"].product == "Health Insurance")
        self.assertEqual(line["added"], Decimal("0"))
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


class MultiyearAccrualTests(_Base):
    """Years 2 and 3 of a multiyear health policy pay on the anniversary."""

    def _policy(self, start, years=3, amount=90000):
        return self.sell(self.health, amount, start, policy_type="fresh",
                         policy_years=years, policy_date=start)

    def test_only_year_one_pays_at_sale_time(self):
        s = self._policy(date(2026, 6, 10))
        self.assertEqual(s.points, Decimal("600.000"))  # 30,000 slice, 2.00% band
        self.assertEqual(s.accruals.count(), 0)

    def test_schedule_is_one_row_per_later_year_on_the_anniversary(self):
        s = self._policy(date(2026, 6, 10))
        sched = inc.accrual_schedule(s)
        self.assertEqual([(n, d) for n, d, _a in sched],
                         [(2, date(2027, 6, 10)), (3, date(2028, 6, 10))])
        self.assertEqual(sched[0][2], Decimal("30000"))

    def test_a_two_year_policy_has_one_later_year(self):
        s = self._policy(date(2026, 6, 10), years=2, amount=60000)
        self.assertEqual([n for n, _d, _a in inc.accrual_schedule(s)], [2])

    def test_single_year_policies_never_accrue(self):
        s = self.sell(self.health, 30000, date(2026, 6, 10),
                      policy_type="fresh", policy_date=date(2026, 6, 10))
        self.assertEqual(inc.accrual_schedule(s), [])

    def test_nothing_issues_before_the_anniversary(self):
        s = self._policy(date(2026, 6, 10))
        self.assertEqual(inc.issue_due_accruals(s, on_date=date(2027, 6, 9)), [])

    def test_year_two_issues_on_the_anniversary(self):
        s = self._policy(date(2026, 6, 10))
        made = inc.issue_due_accruals(s, on_date=date(2027, 6, 10))
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].year_index, 2)
        self.assertEqual(made[0].amount, Decimal("30000"))
        self.assertEqual(made[0].points, Decimal("600.000"))  # 2.00% band

    def test_rerunning_never_pays_the_same_year_twice(self):
        s = self._policy(date(2026, 6, 10))
        inc.issue_due_accruals(s, on_date=date(2027, 6, 10))
        again = inc.issue_due_accruals(s, on_date=date(2027, 6, 10))
        self.assertEqual(again, [])
        self.assertEqual(s.accruals.count(), 1)

    def test_a_missed_run_catches_up_without_duplicating(self):
        s = self._policy(date(2026, 6, 10))
        made = inc.issue_due_accruals(s, on_date=date(2028, 9, 1))
        self.assertEqual(sorted(a.year_index for a in made), [2, 3])
        self.assertEqual(s.accruals.count(), 2)

    def test_a_later_year_is_priced_by_the_month_it_lands_in(self):
        s = self._policy(date(2026, 6, 10))
        # A big Fresh month in June 2027 lifts the year-2 slice into a higher step.
        self.sell(self.health, 280000, date(2027, 6, 1), policy_type="fresh")
        made = inc.issue_due_accruals(s, on_date=date(2027, 6, 10))
        self.assertEqual(made[0].points, Decimal("1050.000"))  # 3.50% band

    def test_the_cron_issues_and_notifies(self):
        from clients.models import Notification

        self._policy(date(2025, 7, 1))  # year 2 fell due 2026-07-01, already past
        call_command("multiyear_incentive_accruals")
        self.assertEqual(
            inc.accrued_points(self.emp, date(2026, 7, 1), date(2026, 7, 31)),
            Decimal("600.000"))
        self.assertTrue(Notification.objects.filter(recipient=self.user).exists())

    def test_dry_run_writes_nothing(self):
        s = self._policy(date(2025, 7, 1))
        call_command("multiyear_incentive_accruals", "--dry-run")
        self.assertEqual(s.accruals.count(), 0)

    def test_pending_list_shows_what_is_still_coming(self):
        self._policy(date(2026, 6, 10))
        rows = inc.pending_accruals(self.emp)
        self.assertEqual([r["year_index"] for r in rows], [2, 3])
        self.assertEqual(rows[0]["amount"], Decimal("30000"))

    def test_rejecting_the_sale_stops_future_years(self):
        s = self._policy(date(2026, 6, 10))
        s.status = Sale.STATUS_REJECTED
        s.save()
        self.assertEqual(inc.accrual_schedule(s), [])
        self.assertEqual(inc.issue_due_accruals(s, on_date=date(2028, 1, 1)), [])


class ExplainerNumbersTests(_Base):
    """The employee page's worked examples must be arithmetic anyone can check."""

    def test_life_totals_are_base_plus_the_prize_once(self):
        rule = IncentiveRule.objects.get(product_ref=self.life)
        e = inc.explain(rule)
        got = [(r["from"], r["bonus"], r["total_at"]) for r in e["rungs"]]
        # base is 1.75%: 3L -> 5,250 + 3,000 = 8,250, and so on up.
        self.assertEqual(got, [
            (Decimal("300000"), Decimal("3000"), Decimal("8250")),
            (Decimal("900000"), Decimal("7500"), Decimal("23250")),
            (Decimal("1800000"), Decimal("20000"), Decimal("51500")),
            (Decimal("3000000"), Decimal("40000"), Decimal("92500")),
            (Decimal("4500000"), Decimal("65000"), Decimal("143750")),
            (Decimal("6000000"), Decimal("90000"), Decimal("195000")),
            (Decimal("7500000"), Decimal("115000"), Decimal("246250")),
            (Decimal("9000000"), Decimal("140000"), Decimal("297500")),
        ])

    def test_the_total_matches_what_selling_exactly_that_much_really_pays(self):
        # Sell precisely the rung amount and compare against the page's figure.
        rule = IncentiveRule.objects.get(product_ref=self.life)
        e = inc.explain(rule)
        today = timezone.localdate()
        sale = self.sell(self.life, 300000, today)
        rung = next(r for r in e["rungs"] if r["from"] == Decimal("300000"))
        self.assertEqual(sale.points, rung["total_at"])

    def test_health_bands_read_as_ranges_priced_per_lakh(self):
        """Each row is a range the month can fall in and one number: what every
        ₹1,00,000 of that month earns. "Once your month reaches X, every ₹10,000
        earns Y" made people read Y as the value of a single sale."""
        rule = IncentiveRule.objects.get(product_ref=self.health)
        e = inc.explain(rule, is_health=True)
        got = [(r["from"], r["upto"], r["per_lakh"]) for r in e["rungs"]]
        self.assertEqual(got, [
            (Decimal("0"), Decimal("25000"), Decimal("1500")),
            (Decimal("25000"), Decimal("50000"), Decimal("2000")),
            (Decimal("50000"), Decimal("100000"), Decimal("2250")),
            (Decimal("100000"), Decimal("200000"), Decimal("2750")),
            (Decimal("200000"), Decimal("300000"), Decimal("3000")),
            (Decimal("300000"), None, Decimal("3500")),   # top band, open-ended
        ])
        # The rate a real sale is paid at must match the row it falls in.
        q = inc.quote(rule, Decimal("100000"), prior_volume=Decimal("0"),
                      policy_type="fresh", is_health=True)
        self.assertEqual(q["total"], Decimal("2750"))     # ₹1L month -> its own row

    def test_no_explainer_text_leaks_a_percentage(self):
        for rule in IncentiveRule.objects.filter(active=True):
            e = inc.explain(rule, is_health=bool(rule.product_ref and rule.product_ref.is_health))
            blob = " ".join([e["headline"], *e["notes"]])
            self.assertNotIn("%", blob, msg=f"{e['name']} leaks a rate")


class BandCrossingTests(_Base):
    """Crossing a health band must reprice the whole month, however the sale
    was entered. The two creation paths used to disagree."""

    def setUp(self):
        self.today = timezone.localdate()

    def _enter(self, amount, *, auto_approve):
        from clients.services import sales as sales_service

        s = Sale(client=self.client_rec, employee=self.emp, product=self.health.name,
                 product_ref=self.health, amount=Decimal(str(amount)),
                 date=self.today, policy_type="fresh")
        return sales_service.finalize_new_sale(s, self.user, auto_approve=auto_approve)

    def test_admin_entered_sale_reprices_the_month(self):
        a = self._enter(20000, auto_approve=True)   # 1.50% band
        b = self._enter(10000, auto_approve=True)   # month hits 30,000 -> 2.00%
        a.refresh_from_db()
        self.assertEqual(a.points, Decimal("400.000"))  # was 300 at the old band
        self.assertEqual(b.points, Decimal("200.000"))
        self.assertEqual(a.points + b.points, Decimal("600.000"))  # 30,000 @ 2.00%

    def test_employee_entered_sale_reprices_on_approval(self):
        from clients.services import sales as sales_service

        a = self._enter(20000, auto_approve=True)
        b = self._enter(10000, auto_approve=False)
        self.assertEqual(Sale.objects.get(pk=a.pk).points, Decimal("300.000"))  # pending: no effect
        sales_service.approve_sale(b, self.user)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.points + b.points, Decimal("600.000"))

    def test_both_entry_paths_pay_the_same(self):
        a = self._enter(20000, auto_approve=True)
        b = self._enter(10000, auto_approve=True)
        a.refresh_from_db()
        admin_total = a.points + b.points

        Sale.objects.all().delete()
        from clients.services import sales as sales_service
        c = self._enter(20000, auto_approve=True)
        d = self._enter(10000, auto_approve=False)
        sales_service.approve_sale(d, self.user)
        c.refresh_from_db()
        d.refresh_from_db()
        self.assertEqual(admin_total, c.points + d.points)

    def test_dropping_back_a_band_reprices_down_again(self):
        a = self._enter(20000, auto_approve=True)
        b = self._enter(10000, auto_approve=True)
        from clients.services import sales as sales_service
        sales_service.delete_sale(b, self.user)
        a.refresh_from_db()
        self.assertEqual(a.points, Decimal("300.000"))  # back to the 1.50% band


class ExplainerCopyTests(_Base):
    """The page's prose has to agree with its own tables."""

    def test_prose_uses_indian_digit_grouping(self):
        rule = IncentiveRule.objects.get(product_ref=self.life)
        e = inc.explain(rule)
        blob = " ".join([e["headline"], e["prize_intro"], *e["notes"]])
        self.assertIn("₹18,00,000", blob)
        self.assertNotIn("1,800,000", blob)  # Western grouping in the copy
        self.assertIn("₹3,00,000", blob)

    def test_life_walkthrough_pays_out_to_the_price_list(self):
        rule = IncentiveRule.objects.get(product_ref=self.life)
        rows = inc.walkthrough(rule)
        # Every prize instalment together equals the level the year finishes on.
        self.assertEqual(sum(r["bonus_paid"] for r in rows), rows[-1]["prize_level"])
        # And the running total is the sale amounts accumulating.
        self.assertEqual(sum(r["amount"] for r in rows), rows[-1]["running"])

    def test_health_walkthrough_shows_the_earlier_sale_being_lifted(self):
        rule = IncentiveRule.objects.get(product_ref=self.health)
        rows = inc.walkthrough(rule, is_health=True)
        first, second = rows
        self.assertIsNone(first["earlier_now"])
        self.assertGreater(second["earlier_now"], first["this_sale"])
        # The month total is the whole month at the new step, not a running sum
        # of what each sale earned when it was made.
        self.assertEqual(second["month_total"],
                         second["earlier_now"] + second["this_sale"])


class PayoutReportTests(_Base):
    """The month's incentive bill and the FY ladder position."""

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.url = reverse("clients:incentive_payout")
        self.today = timezone.localdate()

    def test_splits_base_from_bonus_and_totals_the_bill(self):
        self.sell(self.life, 250000, self.today)
        self.sell(self.life, 60000, self.today)   # crosses 3L -> releases 3,000
        r = self.client.get(self.url, {"month": self.today.month, "year": self.today.year})
        row = next(x for x in r.context["rows"] if x["employee"] == self.emp)
        self.assertEqual(row["bonus"], Decimal("3000.000"))
        self.assertEqual(row["base"], Decimal("5425.000"))   # 1.75% of 3,10,000
        self.assertEqual(row["total"], Decimal("8425.000"))
        self.assertEqual(r.context["totals"]["total"], Decimal("8425.000"))

    def test_names_the_day_the_bonus_was_released(self):
        self.sell(self.life, 250000, self.today)
        crossing = self.sell(self.life, 60000, self.today)
        r = self.client.get(self.url, {"month": self.today.month, "year": self.today.year})
        self.assertIn(crossing, list(r.context["releases"]))
        # A sale that released nothing must not be listed as a release.
        self.assertTrue(all(s.bonus_points > 0 for s in r.context["releases"]))

    def test_shows_what_is_still_unclaimed_on_the_ladder(self):
        self.sell(self.life, 310000, self.today)
        r = self.client.get(self.url, {"month": self.today.month, "year": self.today.year})
        led = next(x for x in r.context["rows"] if x["employee"] == self.emp)["ladder"]
        self.assertEqual(led["level"], Decimal("3000"))
        self.assertEqual(led["released"], Decimal("3000.000"))
        self.assertEqual(led["next"]["slab"].threshold, Decimal("900000"))
        self.assertEqual(led["next"]["worth"], Decimal("4500"))

    def test_multiyear_credits_land_in_the_month_they_fall_due(self):
        s = self.sell(self.health, 90000, self.today, policy_type="fresh",
                      policy_years=3, policy_date=self.today)
        due = inc.accrual_schedule(s)[0][1]
        inc.issue_due_accruals(s, on_date=due)
        r = self.client.get(self.url, {"month": due.month, "year": due.year})
        row = next(x for x in r.context["rows"] if x["employee"] == self.emp)
        self.assertEqual(row["accrued"], Decimal("600.000"))

    def test_employees_cannot_open_it(self):
        plain = User.objects.create_user("plain", password="x")
        Employee.objects.create(user=plain, role="employee")
        self.client.force_login(plain)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)


class MonthlyReportProductTests(_Base):
    """The monthly business report lists categories, never sub-products."""

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.plan = Product.objects.create(
            code="TERM_PLAN", name="Term Plan", parent=self.life)
        self.today = timezone.localdate()

    def test_sub_products_are_not_columns(self):
        r = self.client.get(reverse("clients:monthly_business_report"),
                            {"month": self.today.month, "year": self.today.year})
        self.assertIn("Life Insurance", r.context["products"])
        self.assertNotIn("Term Plan", r.context["products"])

    def test_a_sub_product_sale_rolls_up_into_its_category(self):
        self.sell(self.plan, 100000, self.today)
        r = self.client.get(reverse("clients:monthly_business_report"),
                            {"month": self.today.month, "year": self.today.year})
        self.assertNotIn("Term Plan", r.context["products"])
        idx = r.context["products"].index("Life Insurance")
        row = next(x for x in r.context["rows"] if x["employee"] == self.emp)
        self.assertEqual(row["product_vals"][idx], Decimal("100000"))
        self.assertEqual(r.context["grand_vals"][idx], Decimal("100000"))


class LifeBonusTrackerTests(_Base):
    """FY ladder status per employee, with the legacy monthly figure alongside."""

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.url = reverse("clients:life_bonus_tracker")
        self.rule = IncentiveRule.objects.get(product_ref=self.life)

    def test_status_totals_a_financial_year(self):
        self.sell(self.life, 250000, date(2026, 6, 10))
        self.sell(self.life, 60000, date(2026, 9, 5))    # crosses 3L
        st = inc.life_bonus_status(self.rule, self.emp, 2026)
        self.assertEqual(st["volume"], Decimal("310000"))
        self.assertEqual(st["base"], Decimal("5425.000"))
        self.assertEqual(st["level"], Decimal("3000"))
        self.assertEqual(st["released"], Decimal("3000.000"))

    def test_months_run_april_to_march_and_carry_a_running_total(self):
        st = inc.life_bonus_status(self.rule, self.emp, 2026)
        self.assertEqual([m["month"] for m in st["months"]],
                         [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3])
        # January onward belongs to the next calendar year.
        self.assertEqual(st["months"][9]["year"], 2027)

    def test_running_total_accumulates_across_the_year(self):
        self.sell(self.life, 100000, date(2026, 6, 1))
        self.sell(self.life, 100000, date(2026, 11, 1))
        st = inc.life_bonus_status(self.rule, self.emp, 2026)
        by_month = {m["month"]: m for m in st["months"]}
        self.assertEqual(by_month[6]["running"], Decimal("100000"))
        self.assertEqual(by_month[11]["running"], Decimal("200000"))
        self.assertEqual(st["months"][-1]["running"], Decimal("200000"))

    def test_a_big_single_month_is_never_deducted_from_the_prize(self):
        """The rule the monthly deduction was removed for: ₹3L in one month
        used to cancel the prize it earned. Now the year's volume is all that
        counts, so this pays exactly the ₹3L rung."""
        self.sell(self.life, 300000, date(2026, 6, 10))
        st = inc.life_bonus_status(self.rule, self.emp, 2026)
        self.assertEqual(st["level"], Decimal("3000"))
        self.assertEqual(st["released"], Decimal("3000.000"))
        self.assertEqual(st["shortfall"], Decimal("0"))

    def test_the_prize_follows_the_year_however_it_is_split(self):
        """Three ₹3L months and one ₹9L month must pay the same prize."""
        for m in (6, 7, 8):
            self.sell(self.life, 300000, date(2026, m, 10))
        st = inc.life_bonus_status(self.rule, self.emp, 2026)
        self.assertEqual(st["volume"], Decimal("900000"))
        self.assertEqual(st["level"], Decimal("7500"))     # the ₹9L rung, in full
        self.assertEqual(st["released"], Decimal("7500.000"))
        self.assertEqual(st["shortfall"], Decimal("0"))

    def test_page_renders_and_expands_one_employee(self):
        self.sell(self.life, 310000, date(2026, 6, 10))
        r = self.client.get(self.url, {"fy": 2026, "employee": self.emp.id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["detail"]["employee"], self.emp)
        self.assertEqual(len(r.context["detail"]["months"]), 12)

    def test_employees_cannot_open_it(self):
        plain = User.objects.create_user("plain2", password="x")
        Employee.objects.create(user=plain, role="employee")
        self.client.force_login(plain)
        self.assertEqual(self.client.get(self.url).status_code, 302)


class BonusPayoutNettingTests(_Base):
    """A fixed monthly payout made by hand must reduce what the ladder owes."""

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.rule = IncentiveRule.objects.get(product_ref=self.life)

    def _pay(self, month, amount):
        from clients.models import BonusPayout

        return BonusPayout.objects.create(
            employee=self.emp, rule=self.rule, for_month=month,
            amount=Decimal(str(amount)))

    def test_a_recorded_payout_suppresses_the_ladder(self):
        self._pay(date(2026, 6, 1), 8000)          # old monthly slab, paid by hand
        crossing = self.sell(self.life, 310000, date(2026, 6, 20))
        # Level at 3,10,000 is 3,000, but 8,000 is already in their hands.
        self.assertEqual(crossing.bonus_points, Decimal("0.000"))
        self.assertEqual(crossing.points, Decimal("5425.000"))  # base only

    def test_the_ladder_still_pays_the_shortfall(self):
        self._pay(date(2026, 6, 1), 2000)
        crossing = self.sell(self.life, 310000, date(2026, 6, 20))
        self.assertEqual(crossing.bonus_points, Decimal("1000.000"))  # 3,000 - 2,000

    def test_it_never_claws_back(self):
        self._pay(date(2026, 6, 1), 50000)
        s = self.sell(self.life, 310000, date(2026, 6, 20))
        self.assertEqual(s.bonus_points, Decimal("0.000"))
        self.assertGreater(s.points, Decimal("0"))

    def test_a_payout_outside_the_financial_year_is_not_netted(self):
        self._pay(date(2026, 3, 1), 8000)   # FY2025, not FY2026
        crossing = self.sell(self.life, 310000, date(2026, 6, 20))
        self.assertEqual(crossing.bonus_points, Decimal("3000.000"))

    def test_recording_one_reprices_sales_already_booked(self):
        crossing = self.sell(self.life, 310000, date(2026, 6, 20))
        self.assertEqual(crossing.bonus_points, Decimal("3000.000"))
        r = self.client.post(reverse("clients:record_bonus_payout"), {
            "employee": self.emp.id, "rule": self.rule.id, "fy": 2026,
            "for_month": "2026-06-01", "amount": "8000",
        })
        self.assertEqual(r.status_code, 302)
        crossing.refresh_from_db()
        self.assertEqual(crossing.bonus_points, Decimal("0.000"))

    def test_resubmitting_a_month_replaces_rather_than_stacks(self):
        from clients.models import BonusPayout

        for amount in ("8000", "6000"):
            self.client.post(reverse("clients:record_bonus_payout"), {
                "employee": self.emp.id, "rule": self.rule.id, "fy": 2026,
                "for_month": "2026-06-01", "amount": amount,
            })
        rows = BonusPayout.objects.filter(employee=self.emp, for_month=date(2026, 6, 1))
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().amount, Decimal("6000"))

    def test_zero_clears_the_month(self):
        from clients.models import BonusPayout

        self._pay(date(2026, 6, 1), 8000)
        self.client.post(reverse("clients:record_bonus_payout"), {
            "employee": self.emp.id, "rule": self.rule.id, "fy": 2026,
            "for_month": "2026-06-01", "amount": "0",
        })
        self.assertFalse(BonusPayout.objects.filter(employee=self.emp).exists())

    def test_tracker_shows_paid_and_shortfall(self):
        self._pay(date(2026, 6, 1), 2000)
        self.sell(self.life, 310000, date(2026, 6, 20))
        st = inc.life_bonus_status(self.rule, self.emp, 2026)
        self.assertEqual(st["level"], Decimal("3000"))
        self.assertEqual(st["paid_manually"], Decimal("2000"))
        self.assertEqual(st["released"], Decimal("3000.000"))  # 1,000 sale + 2,000 hand
        self.assertEqual(st["shortfall"], Decimal("0"))

    def test_extended_ladder_has_no_ceiling_at_45_lakh(self):
        rungs = {s.threshold: s.payout for s in self.rule.slabs.all()}
        self.assertEqual(rungs[Decimal("6000000")], Decimal("90000"))
        self.assertEqual(rungs[Decimal("7500000")], Decimal("115000"))
        self.assertEqual(rungs[Decimal("9000000")], Decimal("140000"))
        # And it keeps climbing rather than flattening onto the base.
        self.assertIsNotNone(inc.next_rung(self.rule, Decimal("5000000")))


class AppIncentiveApiTests(_Base):
    """The mobile payload has to say what a slab payout MEANS."""

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)

    def _rules(self):
        import json

        r = self.client.get(reverse("clients:app_incentives"))
        self.assertEqual(r.status_code, 200)
        return {x["product"]: x for x in json.loads(r.content)["rules"]}

    def test_health_is_flagged_as_percent_bands(self):
        rules = self._rules()
        h = rules["Health Insurance"]
        self.assertEqual(h["slab_unit"], "percent")
        self.assertEqual(h["slab_period"], "month")
        self.assertNotIn("port_percent", h)   # Port earns nothing; no rate to send

    def test_life_is_flagged_as_a_points_ladder_over_the_year(self):
        life = self._rules()["Life Insurance"]
        self.assertEqual(life["slab_unit"], "points")
        self.assertEqual(life["slab_period"], "fy")
        self.assertNotIn("port_percent", life)

    def test_a_rupee_amount_cannot_be_saved_as_a_rate_band(self):
        import json

        rule = IncentiveRule.objects.get(product_ref=self.health)
        slab = rule.slabs.get(threshold=Decimal("25000"))
        r = self.client.post(
            reverse("clients:update_incentive_slab", args=[slab.id]),
            data=json.dumps({"payout": "500"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        slab.refresh_from_db()
        self.assertEqual(slab.payout, Decimal("2.00"))   # untouched

    def test_the_same_guard_applies_when_adding(self):
        import json

        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.post(
            reverse("clients:add_incentive_slab", args=[rule.id]),
            data=json.dumps({"threshold": "400000", "payout": "8000"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(rule.slabs.filter(threshold=Decimal("400000")).exists())

    def test_a_real_percentage_still_saves(self):
        import json

        rule = IncentiveRule.objects.get(product_ref=self.health)
        r = self.client.post(
            reverse("clients:add_incentive_slab", args=[rule.id]),
            data=json.dumps({"threshold": "500000", "payout": "4.00"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(rule.slabs.filter(threshold=Decimal("500000")).exists())

    def test_the_ladder_is_not_capped_at_100(self):
        import json

        rule = IncentiveRule.objects.get(product_ref=self.life)
        r = self.client.post(
            reverse("clients:add_incentive_slab", args=[rule.id]),
            data=json.dumps({"threshold": "12000000", "payout": "180000"}),
            content_type="application/json")
        self.assertEqual(r.status_code, 200)


class CalculatorLadderStatusTests(_Base):
    """The calculator has to show where the year's ladder stands, not just hint."""

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("clients:incentive_calculator")
        self.today = timezone.localdate()

    def test_life_ladder_status_is_on_the_page(self):
        self.sell(self.life, 310000, self.today)
        r = self.client.get(self.url)
        st = next(s for s in r.context["ladder_status"]
                  if s["rule"].product == "Life Insurance")
        self.assertEqual(st["volume"], Decimal("310000"))
        self.assertEqual(st["level"], Decimal("3000"))
        self.assertEqual(len(st["rungs"]), 8)

    def test_health_has_no_ladder_status(self):
        # Health is rate-banded, not a ladder — it must not appear here.
        self.sell(self.health, 50000, self.today, policy_type="fresh")
        r = self.client.get(self.url)
        self.assertNotIn("Health Insurance",
                         [s["rule"].product for s in r.context["ladder_status"]])

    def test_shortfall_is_what_the_year_still_owes(self):
        from clients.models import BonusPayout

        self.sell(self.life, 310000, self.today)
        r = self.client.get(self.url)
        st = r.context["ladder_status"][0]
        self.assertEqual(st["shortfall"], Decimal("0"))  # released with the sale

        BonusPayout.objects.create(
            employee=self.emp, rule=IncentiveRule.objects.get(product_ref=self.life),
            for_month=self.today.replace(day=1), amount=Decimal("8000"))
        r = self.client.get(self.url)
        st = r.context["ladder_status"][0]
        self.assertEqual(st["paid_manually"], Decimal("8000"))
        self.assertEqual(st["shortfall"], Decimal("0"))   # monthly payout covers it

    def test_an_employee_sees_only_their_own_standing(self):
        other_user = User.objects.create_user("rival2", password="x")
        other = Employee.objects.create(user=other_user, role="employee")
        Sale.objects.create(client=self.client_rec, employee=other,
                            product=self.life.name, product_ref=self.life,
                            amount=Decimal("900000"), date=self.today,
                            status=Sale.STATUS_APPROVED)
        r = self.client.get(self.url, {"employee": other.id})
        st = r.context["ladder_status"][0]
        self.assertEqual(st["employee"], self.emp)
        self.assertEqual(st["volume"], Decimal("0"))


class AdminTableRenderingTests(_Base):
    """Two tables that rendered badly rather than wrongly.

    The month strip drew a payment form on all twelve rows, so a year with
    one month of business showed eleven rows of em-dashes each with its own
    input box. The payout page's ladder table filtered its rows inside the
    loop, so with no life business it drew a header with nothing under it.
    """

    def setUp(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def test_the_month_strip_only_offers_a_form_where_there_is_business(self):
        self.sell(self.life, 950000, date(2026, 7, 10))
        html = self.client.get(reverse("clients:life_bonus_tracker"),
                               {"fy": 2026, "employee": self.emp.id}).content.decode()
        # One row has business, so exactly one payment form is drawn.
        self.assertEqual(html.count('name="for_month"'), 1)
        self.assertIn('value="2026-07-01"', html)

    def test_a_month_with_a_recorded_payment_keeps_its_form(self):
        from clients.models import BonusPayout

        rule = IncentiveRule.objects.get(product_ref=self.life)
        BonusPayout.objects.create(employee=self.emp, rule=rule,
                                   for_month=date(2026, 9, 1), amount=Decimal("2000"))
        self.sell(self.life, 950000, date(2026, 7, 10))
        html = self.client.get(reverse("clients:life_bonus_tracker"),
                               {"fy": 2026, "employee": self.emp.id}).content.decode()
        self.assertEqual(html.count('name="for_month"'), 2)   # July + September

    def test_the_payout_ladder_table_says_when_it_is_empty(self):
        resp = self.client.get(reverse("clients:incentive_payout"),
                               {"month": self.today.month, "year": self.today.year})
        self.assertEqual(resp.context["ladder_rows"], [])
        self.assertContains(resp, "No life business booked this financial year")

    def test_the_payout_ladder_table_lists_only_people_with_volume(self):
        self.sell(self.life, 500000, self.today)
        resp = self.client.get(reverse("clients:incentive_payout"),
                               {"month": self.today.month, "year": self.today.year})
        self.assertEqual([r["employee"] for r in resp.context["ladder_rows"]], [self.emp])


class CalculatorRosterTests(_Base):
    """The yearly bonus section lists everyone — for people allowed to see it."""

    def setUp(self):
        self.url = reverse("clients:incentive_calculator")
        self.today = timezone.localdate()
        self.other_user = User.objects.create_user("colleague", password="x")
        self.other = Employee.objects.create(user=self.other_user, role="employee")
        self.sell(self.life, 310000, self.today)
        Sale.objects.create(client=self.client_rec, employee=self.other,
                            product=self.life.name, product_ref=self.life,
                            amount=Decimal("900000"), date=self.today,
                            status=Sale.STATUS_APPROVED)

    def _status(self, user):
        self.client.force_login(user)
        r = self.client.get(self.url)
        return r.context["ladder_status"][0]

    def test_an_admin_sees_every_employee(self):
        boss = User.objects.create_user("boss3", password="x")
        Employee.objects.create(user=boss, role="admin")
        roster = self._status(boss)["roster"]
        self.assertEqual({r["employee"] for r in roster}, {self.emp, self.other})

    def test_the_roster_carries_sold_given_and_pending(self):
        boss = User.objects.create_user("boss4", password="x")
        Employee.objects.create(user=boss, role="admin")
        roster = {r["employee"]: r for r in self._status(boss)["roster"]}
        mine = roster[self.emp]
        self.assertEqual(mine["volume"], Decimal("310000"))
        self.assertEqual(mine["level"], Decimal("3000"))
        self.assertEqual(mine["released"], Decimal("3000.000"))
        self.assertEqual(mine["pending"], Decimal("0"))

    def test_pending_is_what_the_ladder_still_owes(self):
        # A sale booked before the structure changed released nothing, so the
        # level is reached but the money has not gone out.
        stale = Employee.objects.create(
            user=User.objects.create_user("stale", password="x"), role="employee")
        s = Sale.objects.create(client=self.client_rec, employee=stale,
                                product=self.life.name, product_ref=self.life,
                                amount=Decimal("310000"), date=self.today,
                                status=Sale.STATUS_APPROVED)
        Sale.objects.filter(pk=s.pk).update(points=0, bonus_points=0)
        boss = User.objects.create_user("boss5", password="x")
        Employee.objects.create(user=boss, role="admin")
        row = next(r for r in self._status(boss)["roster"] if r["employee"] == stale)
        self.assertEqual(row["pending"], Decimal("3000"))

    def test_the_roster_is_sorted_by_volume(self):
        boss = User.objects.create_user("boss6", password="x")
        Employee.objects.create(user=boss, role="admin")
        roster = self._status(boss)["roster"]
        self.assertEqual(roster[0]["employee"], self.other)   # 9L beats 3.1L

    def test_a_plain_employee_gets_no_roster(self):
        st = self._status(self.user)
        self.assertEqual(st["roster"], [])
        self.assertEqual(st["volume"], Decimal("310000"))   # own standing still shown



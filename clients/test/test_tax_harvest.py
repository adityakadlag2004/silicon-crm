"""Tax harvesting: the yearly LTCG harvest per client, done vs pending.

Pins the FY bucketing (31 March vs 1 April), the pending rule (harvested in an
earlier FY, nothing in this one — a 0 "reviewed" entry counts as done), the
stop flag, the next-harvest countdown, the limit by year, the screens, and that
the profile tells the reader to add the harvested gain back onto the
statement's profit.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client, Employee, TaxHarvest
from clients.services import tax_harvest as th
from clients.services.incentives import fy_start_year


def _harvest(client, on, gain, **kw):
    return TaxHarvest.objects.create(client=client, date=on, gain_booked=Decimal(gain), **kw)


class BookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.lapsed = Client.objects.create(name="Harvested Last Year")
        cls.repeat = Client.objects.create(name="Harvested Both Years")
        cls.reviewed = Client.objects.create(name="Reviewed Nil")
        Client.objects.create(name="Never Harvested")
        _harvest(cls.lapsed, date(2026, 3, 31), 120000, portfolio_value=900000)   # FY 2025-26
        _harvest(cls.repeat, date(2025, 6, 10), 100000)
        _harvest(cls.repeat, date(2026, 4, 1), 90000)                             # FY 2026-27
        _harvest(cls.repeat, date(2026, 5, 1), 50000)
        _harvest(cls.reviewed, date(2025, 5, 1), 80000)
        _harvest(cls.reviewed, date(2026, 6, 1), 0)

    TODAY = date(2026, 10, 5)

    def test_pending_is_last_years_harvesters_with_nothing_this_year(self):
        done, pending, _ = th.book(2026, self.TODAY)
        self.assertEqual({r["client"].name for r in done}, {"Harvested Both Years", "Reviewed Nil"})
        self.assertEqual([r["client"].name for r in pending], ["Harvested Last Year"])
        # 31 March belongs to the year that is ending, 1 April to the new one.
        done, pending, _ = th.book(2025, self.TODAY)
        self.assertEqual({r["client"].name for r in done},
                         {"Harvested Last Year", "Harvested Both Years", "Reviewed Nil"})
        self.assertEqual(pending, [])

    def test_a_year_over_the_limit_says_by_how_much(self):
        done, _, _ = th.book(2026, self.TODAY)
        row = next(r for r in done if r["client"] == self.repeat)
        self.assertEqual((row["fy_gain"], row["over"], row["headroom"]),
                         (Decimal(140000), Decimal(15000), Decimal(0)))
        self.assertEqual(row["tax_saved"], Decimal(125000) * th.TAX_RATE)  # only the exempt slice
        self.assertEqual(row["total_gain"], Decimal(240000))

    def test_limit_was_one_lakh_until_fy_2023_24(self):
        self.assertEqual((th.exemption(2023), th.exemption(2024)), (100000, 125000))

    def test_next_harvest_is_a_year_on_when_the_units_turn_long_term(self):
        _, pending, _ = th.book(2026, self.TODAY)
        row = pending[0]
        # Held MORE than 12 months: 31 Mar 2026 → 1 Apr 2027.
        self.assertEqual((row["next_harvest"], row["days"], row["overdue"]),
                         (date(2027, 4, 1), 178, 0))
        self.assertEqual(row["portfolio_value"], Decimal(900000))
        _, pending, _ = th.book(2026, date(2027, 4, 11))
        self.assertEqual(pending[0]["overdue"], 10)

    def test_a_stopped_client_is_never_pending_but_keeps_its_history(self):
        Client.objects.filter(pk__in=[self.lapsed.pk, self.repeat.pk]).update(tax_harvest_stopped=True)
        done, pending, stopped = th.book(2026, self.TODAY)
        self.assertEqual(pending, [])
        self.assertEqual([r["client"].name for r in stopped],
                         ["Harvested Both Years", "Harvested Last Year"])
        # What a stopped client booked this year still happened.
        self.assertIn(self.repeat, [r["client"] for r in done])


class ScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("th_admin", password="x")
        Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.rm = User.objects.create_user("th_rm", password="x")
        Employee.objects.create(user=cls.rm, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Tax Harvest Client")
        fy = fy_start_year(timezone.localdate())
        cls.fy_start = date(fy, 4, 1)
        # No recorder on purpose: imported rows, or a deleted login, leave it blank.
        cls.old = _harvest(cls.customer, date(fy - 1, 6, 1), 125000)

    def setUp(self):
        self.client.force_login(self.rm)

    def test_list_opens_on_pending_and_profile_wears_the_tag(self):
        resp = self.client.get(reverse("clients:tax_harvest_list"))
        self.assertEqual(resp.context["tab"], "pending")
        self.assertContains(resp, "Tax Harvest Client")
        profile = self.client.get(reverse("clients:client_profile", args=[self.customer.pk]))
        self.assertContains(profile, 'data-tab="tax"')
        self.assertContains(profile, "Tax harvest pending")
        self.assertContains(profile, "Real profit = statement profit + ₹1,25,000")

    def test_recording_this_year_moves_the_client_to_done(self):
        resp = self.client.post(reverse("clients:tax_harvest_add"), {
            "client": self.customer.pk, "date": self.fy_start.isoformat(),
            "gain_booked": "100000", "details": "Sold 500 units, reinvested same day"})
        self.assertRedirects(resp, reverse("clients:client_profile", args=[self.customer.pk]) + "#tax",
                             fetch_redirect_response=False)
        new = TaxHarvest.objects.latest("id")
        self.assertEqual((new.created_by, new.portfolio_value), (self.rm, 0))  # blank → 0
        today = timezone.localdate()
        done, pending, _ = th.book(fy_start_year(today), today)
        self.assertEqual(([r["client"] for r in done], pending), ([self.customer], []))

    def test_booking_past_the_limit_is_recorded_and_flagged(self):
        url = reverse("clients:tax_harvest_add")
        data = {"client": self.customer.pk, "date": self.fy_start.isoformat()}
        self.client.post(url, {**data, "gain_booked": "100000"})
        resp = self.client.post(url, {**data, "gain_booked": "40000"}, follow=True)
        self.assertEqual(TaxHarvest.objects.count(), 3)
        self.assertContains(resp, "over the ₹1,25,000")

    def test_future_date_is_refused(self):
        self.client.post(reverse("clients:tax_harvest_add"), {
            "client": self.customer.pk, "gain_booked": "1000",
            "date": (timezone.localdate() + timedelta(days=1)).isoformat()})
        self.assertEqual(TaxHarvest.objects.count(), 1)

    def test_only_an_admin_or_whoever_recorded_it_may_delete(self):
        url = reverse("clients:tax_harvest_delete", args=[self.old.pk])
        self.client.post(url)
        self.assertTrue(TaxHarvest.objects.filter(pk=self.old.pk).exists())
        self.client.force_login(self.admin)
        self.client.post(url)
        self.assertFalse(TaxHarvest.objects.filter(pk=self.old.pk).exists())

    def test_stop_and_resume_from_the_list(self):
        url = reverse("clients:tax_harvest_stop", args=[self.customer.pk])
        back = reverse("clients:tax_harvest_list") + "?tab=pending"
        resp = self.client.post(url, {"stop": "1", "next": back})
        self.assertRedirects(resp, back, fetch_redirect_response=False)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.tax_harvest_stopped)
        page = self.client.get(back)
        self.assertEqual((len(page.context["pending"]), len(page.context["stopped"])), (0, 1))
        profile = self.client.get(reverse("clients:client_profile", args=[self.customer.pk]))
        self.assertNotContains(profile, "Tax harvest pending")
        self.client.post(url, {"stop": "0", "next": "https://evil.example/"})
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.tax_harvest_stopped)


class ReinvestmentStageTests(TestCase):
    """Selling is 50%; the harvest is complete once the money is back in."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("th_stage", password="x")
        Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Stage Client")
        cls.sold_on = timezone.localdate() - timedelta(days=5)

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, harvest=None, **data):
        url = (reverse("clients:tax_harvest_edit", args=[harvest.pk]) if harvest
               else reverse("clients:tax_harvest_add"))
        base = {"client": self.customer.pk, "date": self.sold_on.isoformat(), "gain_booked": "120000"}
        return self.client.post(url, {**base, **data}, follow=True)

    def test_a_sale_alone_is_half_done_and_waits_in_the_queue(self):
        resp = self._post()
        h = TaxHarvest.objects.get()
        self.assertEqual((h.progress, h.reinvestment, h.reinvested_on), (50, "", None))
        self.assertContains(resp, "50% done")
        page = self.client.get(reverse("clients:tax_harvest_list") + "?tab=awaiting")
        self.assertEqual(page.context["awaiting"], [h])
        self.assertContains(page, "Mark Repurchased")

    def test_repurchase_or_insurance_conversion_completes_it(self):
        self._post()
        h = TaxHarvest.objects.get()
        on = (self.sold_on + timedelta(days=1)).isoformat()
        for kind in (TaxHarvest.REINVEST_MF, TaxHarvest.REINVEST_INSURANCE):
            self._post(h, reinvestment=kind, reinvested_on=on)
            h.refresh_from_db()
            self.assertEqual((h.progress, h.reinvestment), (100, kind))
        self.assertEqual(list(th.awaiting()), [])

    def test_the_repurchase_needs_a_date_not_before_the_sale(self):
        self._post(reinvestment=TaxHarvest.REINVEST_MF)
        self._post(reinvestment=TaxHarvest.REINVEST_MF,
                   reinvested_on=(self.sold_on - timedelta(days=1)).isoformat())
        self.assertFalse(TaxHarvest.objects.exists())

    def test_a_nil_review_sold_nothing_so_it_is_complete(self):
        h = _harvest(self.customer, self.sold_on, 0)
        self.assertEqual(h.progress, 100)
        self.assertEqual(list(th.awaiting()), [])

    def test_next_harvest_counts_from_the_repurchase_not_the_sale(self):
        h = _harvest(self.customer, date(2026, 3, 20), 125000,
                     reinvestment=TaxHarvest.REINVEST_MF, reinvested_on=date(2026, 3, 27))
        self.assertEqual(th.next_harvest(h, date(2026, 10, 5))["next_harvest"], date(2027, 3, 28))
        h.reinvestment = TaxHarvest.REINVEST_INSURANCE      # no units bought back
        self.assertEqual(th.next_harvest(h, date(2026, 10, 5))["next_harvest"], date(2027, 3, 21))

"""Team screens: active and inactive are separate lists, and an employee's
detail page is their whole record with the firm.

The number that matters most here is "points earned": `Sale.points` alone
understates it, because multiyear health years land as `IncentiveAccrual`
rows and the financial-year ladder prize is handed over as a `BonusPayout`.
Any total shown to or about an employee has to add all three.

Run: .venv/bin/python manage.py test clients.test.test_team_performance
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import (
    BonusPayout, Client, Employee, EmployeeTarget, IncentiveAccrual,
    IncentiveRule, Lead, Renewal, Sale, Task,
)
from clients.services import employee_performance as perf


def _mk_employee(username, role="employee", active=True):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=active)


class TeamListSplitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk_employee("tp_admin", role="admin")
        cls.leaver = _mk_employee("tp_leaver", active=False)

    def _get(self, **params):
        client = TestClient()
        client.force_login(self.admin.user)
        return client.get(reverse("clients:team_list"), params)

    def test_the_page_opens_on_active_people_only(self):
        res = self._get()
        self.assertEqual(res.context["status_filter"], "active")
        self.assertNotIn(self.leaver, res.context["employees"])
        self.assertIn(self.admin, res.context["employees"])

    def test_the_inactive_tab_shows_only_leavers(self):
        res = self._get(status="inactive")
        self.assertEqual(list(res.context["employees"]), [self.leaver])

    def test_the_all_tab_shows_both(self):
        res = self._get(status="all")
        self.assertIn(self.leaver, res.context["employees"])
        self.assertIn(self.admin, res.context["employees"])

    def test_a_search_stays_inside_the_current_tab(self):
        res = self._get(status="inactive", q="tp_")
        self.assertEqual(list(res.context["employees"]), [self.leaver])


class ListStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("tp_stats")
        cls.client_row = Client.objects.create(name="STATS CLIENT")
        EmployeeTarget.objects.create(employee=cls.emp, product="SIP", target_value=Decimal("10000"))
        Sale.objects.create(client=cls.client_row, employee=cls.emp, product="SIP",
                            amount=Decimal("2500"), date=date.today(), status=Sale.STATUS_APPROVED)
        Task.objects.create(title="Late one", assigned_to=cls.emp,
                            due_date=date.today() - timedelta(days=2))

    def test_headline_numbers_per_employee(self):
        row = perf.list_stats([self.emp])[self.emp.id]
        self.assertEqual(row["month_sales"], Decimal("2500"))
        self.assertEqual(row["target"], Decimal("10000"))
        self.assertEqual(row["pct"], 25.0)
        self.assertEqual(row["open_tasks"], 1)
        self.assertEqual(row["overdue_tasks"], 1)

    def test_no_target_reports_none_rather_than_zero_percent(self):
        other = _mk_employee("tp_no_target")
        self.assertIsNone(perf.list_stats([other])[other.id]["pct"])

    def test_an_empty_team_needs_no_queries(self):
        self.assertEqual(perf.list_stats([]), {})


class SnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_employee("tp_snap")
        cls.client_row = Client.objects.create(name="SNAP CLIENT", mapped_to=cls.emp)
        cls.sale = Sale.objects.create(
            client=cls.client_row, employee=cls.emp, product="Life Insurance",
            amount=Decimal("100000"), date=date.today(), status=Sale.STATUS_APPROVED)
        Sale.objects.filter(pk=cls.sale.pk).update(points=Decimal("1750"))
        Renewal.objects.create(
            client=cls.client_row, employee=cls.emp,
            product_type=Renewal.PRODUCT_TYPE_HEALTH, renewal_date=date.today(),
            premium_collected_on=date.today(), frequency=Renewal.FREQUENCY_YEARLY,
            premium_amount=Decimal("20000"))

    def test_business_adds_sales_and_renewal_premium(self):
        snap = perf.snapshot(self.emp)
        self.assertEqual(snap["business"]["sales_all"], Decimal("100000"))
        self.assertEqual(snap["business"]["renewal_all"], Decimal("20000"))
        self.assertEqual(snap["business"]["total_all"], Decimal("120000"))

    def test_points_earned_adds_accruals_and_hand_paid_prizes(self):
        rule = IncentiveRule.objects.create(
            product="Life Insurance", unit_amount=Decimal("100"),
            points_per_unit=Decimal("1.75"))
        IncentiveAccrual.objects.create(
            sale=self.sale, employee=self.emp, year_index=2,
            due_date=date.today(), amount=Decimal("50000"), points=Decimal("875"))
        BonusPayout.objects.create(
            employee=self.emp, rule=rule, for_month=date.today().replace(day=1),
            amount=Decimal("3000"))

        earnings = perf.snapshot(self.emp)["earnings"]
        self.assertEqual(earnings["sale_points"], Decimal("1750"))
        self.assertEqual(earnings["accrued_points"], Decimal("875"))
        self.assertEqual(earnings["bonus_payouts"], Decimal("3000"))
        self.assertEqual(earnings["total"], Decimal("5625"),
                         "sales points alone understate what somebody earned")

    def test_target_attainment_is_none_without_a_target(self):
        self.assertIsNone(perf.snapshot(self.emp)["target"]["pct"])

    def test_target_attainment_is_measured_on_this_months_sales(self):
        EmployeeTarget.objects.create(
            employee=self.emp, product="Life Insurance", target_value=Decimal("200000"))
        target = perf.snapshot(self.emp)["target"]
        self.assertEqual(target["monthly"], Decimal("200000"))
        self.assertEqual(target["pct"], 50.0)

    def test_pipeline_win_rate_counts_orders(self):
        Lead.objects.create(customer_name="Won one", assigned_to=self.emp,
                            stage=Lead.STAGE_ORDER)
        Lead.objects.create(customer_name="Open one", assigned_to=self.emp,
                            stage=Lead.STAGE_APPROACH)
        pipeline = perf.snapshot(self.emp)["pipeline"]
        self.assertEqual(pipeline["total"], 2)
        self.assertEqual(pipeline["won"], 1)
        self.assertEqual(pipeline["open"], 1)
        self.assertEqual(pipeline["win_pct"], 50.0)

    def test_client_value_counts_sales_and_renewals_of_mapped_clients(self):
        clients = perf.snapshot(self.emp)["clients"]
        self.assertEqual(clients["count"], 1)
        self.assertEqual(clients["transacted"], Decimal("120000"))
        self.assertEqual(clients["top"][0].id, self.client_row.id)

    def test_the_trend_covers_thirteen_months_ending_this_one(self):
        trend = perf.snapshot(self.emp)["trend"]
        self.assertEqual(len(trend), 13)
        self.assertEqual(trend[-1]["month"], date.today().replace(day=1))
        self.assertEqual(trend[-1]["total"], Decimal("120000"))

    def test_the_detail_page_renders_the_whole_record(self):
        admin = _mk_employee("tp_snap_admin", role="admin")
        client = TestClient()
        client.force_login(admin.user)
        html = client.get(
            reverse("clients:team_detail", args=[self.emp.id])).content.decode()
        for needle in ("Points earned", "against target", "Last 12 months",
                       "Pipeline", "Recent Renewals", "Transacted w/ clients"):
            self.assertIn(needle, html)

        # Presence alone passes happily on a page covered in leaked template
        # syntax, so assert the absences too. team_detail takes an id, so the
        # repo-wide scan in test_theme_coverage never renders it.
        import re
        body = re.sub(r"<(script|style)\b.*?</\1>", "",
                      html.split("<body", 1)[-1], flags=re.S | re.I)
        for marker in ("{#", "#}", "{%", "%}", "{{", "}}", "None", "Decimal("):
            self.assertNotIn(marker, body, f"{marker!r} leaked into the page")

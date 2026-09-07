"""Health EMI plans: the schedule is derived from the sale, and the page splits
live collection from finished plans."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clients.models import Client, Employee, InsurancePolicy, Product, Sale
from clients.services.sales import emi_schedule


class EmiScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.emp = Employee.objects.create(
            user=User.objects.create_user("emiseller", password="x"),
            role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="EMI Payer")

    def _sale(self, on, months=5, number="EMI-A"):
        return Sale.objects.create(
            client=self.customer, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("300000"), policy_type="fresh",
            policy_years=3, emi_months=months, status=Sale.STATUS_APPROVED,
            date=on, policy_date=on, policy_number=number)

    def test_window_starts_month_after_sale_and_crosses_the_year(self):
        s = self._sale(date(2026, 11, 3))
        r = emi_schedule(s, date(2026, 11, 20))
        self.assertEqual((r["start"], r["end"]), (date(2026, 12, 5), date(2027, 4, 5)))
        self.assertEqual((r["paid"], r["pending"], r["state"]), (0, 5, "live"))
        self.assertEqual(r["next_due"], date(2026, 12, 5))
        self.assertEqual(r["monthly"], Decimal("60000"))

    def test_live_until_the_last_instalment_falls_due(self):
        s = self._sale(date(2026, 6, 10))
        self.assertEqual(emi_schedule(s, date(2026, 9, 6))["paid"], 3)
        self.assertEqual(emi_schedule(s, date(2026, 11, 4))["state"], "live")
        self.assertEqual(emi_schedule(s, date(2026, 11, 5))["state"], "completed")

    def test_not_on_emi_has_no_schedule(self):
        self.assertIsNone(emi_schedule(self._sale(date(2026, 6, 10), months=0, number="EMI-B")))

    def test_cancelled_policy_reads_stopped(self):
        s = self._sale(date(2026, 6, 10), number="EMI-C")
        InsurancePolicy.objects.create(
            client=self.customer, source_sale=s, policy_number="EMI-C",
            insurance_type="health", status=InsurancePolicy.STATUS_CANCELLED)
        self.assertEqual(emi_schedule(Sale.objects.get(pk=s.pk), date(2026, 8, 6))["state"], "stopped")


class EmiListPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.life, _ = Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance"})
        cls.emp = Employee.objects.create(
            user=User.objects.create_user("emiview", password="x"),
            role="employee", salary=0, active=True)
        cls.live_client = Client.objects.create(name="Live Payer")
        cls.done_client = Client.objects.create(name="Done Payer")
        today = date.today()
        Sale.objects.create(
            client=cls.live_client, employee=cls.emp, product="Health Insurance",
            product_ref=cls.health, amount=Decimal("300000"), policy_type="fresh",
            policy_years=3, emi_months=11, status=Sale.STATUS_APPROVED,
            date=today, policy_date=today, policy_number="LIVE1")
        old = date(today.year - 3, 1, 10)
        Sale.objects.create(
            client=cls.done_client, employee=cls.emp, product="Health Insurance",
            product_ref=cls.health, amount=Decimal("300000"), policy_type="fresh",
            policy_years=3, emi_months=5, status=Sale.STATUS_APPROVED,
            date=old, policy_date=old, policy_number="DONE1")
        # Neither of these belongs on the page: no EMI, and not health.
        Sale.objects.create(
            client=cls.live_client, employee=cls.emp, product="Health Insurance",
            product_ref=cls.health, amount=Decimal("50000"), policy_type="fresh",
            date=today, policy_date=today, policy_number="NOEMI1",
            status=Sale.STATUS_APPROVED)
        Sale.objects.create(
            client=cls.live_client, employee=cls.emp, product="Life Insurance",
            product_ref=cls.life, amount=Decimal("90000"), policy_years=3, emi_months=5,
            date=today, policy_date=today, policy_number="LIFE1",
            status=Sale.STATUS_APPROVED)

    def setUp(self):
        self.client.force_login(self.emp.user)

    def test_page_lists_emi_health_sales_only(self):
        r = self.client.get(reverse("clients:emi_list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["sale"].policy_number for x in r.context["rows"]], ["LIVE1", "DONE1"])

    def test_state_tabs_split_live_from_completed(self):
        live = self.client.get(reverse("clients:emi_list"), {"state": "live"})
        self.assertEqual([x["sale"].policy_number for x in live.context["rows"]], ["LIVE1"])
        done = self.client.get(reverse("clients:emi_list"), {"state": "completed"})
        self.assertEqual([x["sale"].policy_number for x in done.context["rows"]], ["DONE1"])

    def test_search_by_client_name(self):
        r = self.client.get(reverse("clients:emi_list"), {"q": "Done"})
        self.assertEqual([x["sale"].policy_number for x in r.context["rows"]], ["DONE1"])

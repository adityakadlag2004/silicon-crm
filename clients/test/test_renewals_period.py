"""The renewals list opens on this month's business, and each date bound works.

Typing two dates before you can see the day's renewal business is the thing
being fixed; a lone "Payment Start" filtering nothing at all is the bug that
was fixed for sales in 7ce020a and left behind here.

Run: .venv/bin/python manage.py test clients.test.test_renewals_period
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, Renewal


class RenewalPeriodTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="ren_admin", password="x")
        cls.emp = Employee.objects.create(user=user, role="admin", salary=0, active=True)
        cls.client_row = Client.objects.create(name="RENEWAL CLIENT")

        today = date.today()
        cls.this_month = cls._renewal(today.replace(day=1), 1000)
        last = today.replace(day=1) - timedelta(days=1)
        cls.last_month = cls._renewal(last, 2000)
        # Far enough back to sit outside this financial year in either half.
        cls.ancient = cls._renewal(today.replace(year=today.year - 3), 4000)

    @classmethod
    def _renewal(cls, collected_on, amount):
        return Renewal.objects.create(
            client=cls.client_row, employee=cls.emp,
            product_type=Renewal.PRODUCT_TYPE_HEALTH,
            renewal_date=collected_on, premium_collected_on=collected_on,
            frequency=Renewal.FREQUENCY_YEARLY, premium_amount=amount,
        )

    def _ids(self, **params):
        client = TestClient()
        client.force_login(self.emp.user)
        res = client.get(reverse("clients:all_renewals"), params)
        self.assertEqual(res.status_code, 200)
        return {r.id for r in res.context["renewals"]}, res.context

    def test_the_page_opens_on_this_month(self):
        ids, ctx = self._ids()
        self.assertEqual(ctx["period"], "month")
        self.assertEqual(ids, {self.this_month.id})

    def test_last_month_chip(self):
        ids, ctx = self._ids(period="last_month")
        self.assertEqual(ctx["period"], "last_month")
        self.assertEqual(ids, {self.last_month.id})

    def test_all_time_chip_drops_the_window(self):
        ids, ctx = self._ids(period="all")
        self.assertEqual(ctx["period"], "all")
        self.assertEqual(
            ids, {self.this_month.id, self.last_month.id, self.ancient.id})

    def test_financial_year_chip_covers_april_to_march(self):
        _, ctx = self._ids(period="fy")
        start_year = date.today().year if date.today().month >= 4 else date.today().year - 1
        self.assertEqual(ctx["filter_payment_start"], f"{start_year}-04-01")
        self.assertEqual(ctx["filter_payment_end"], f"{start_year + 1}-03-31")

    def test_searching_keeps_the_month_window(self):
        # The month default used to vanish the moment anything was typed.
        _, ctx = self._ids(q="RENEWAL")
        self.assertEqual(ctx["period"], "month")
        self.assertTrue(ctx["filter_payment_start"])

    def test_a_start_date_alone_filters(self):
        ids, ctx = self._ids(payment_start=date.today().replace(day=1).isoformat())
        self.assertEqual(ctx["period"], "custom")
        self.assertEqual(ids, {self.this_month.id})

    def test_an_end_date_alone_filters(self):
        cutoff = (date.today().replace(day=1) - timedelta(days=1)).isoformat()
        ids, _ = self._ids(payment_end=cutoff)
        self.assertEqual(ids, {self.last_month.id, self.ancient.id})

    def test_an_unknown_period_falls_back_to_this_month(self):
        _, ctx = self._ids(period="nonsense")
        self.assertEqual(ctx["period"], "month")

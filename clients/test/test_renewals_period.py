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


class RenewalTypeTabTests(TestCase):
    """Health and Life are separate books, and the page must read one at a time."""

    @classmethod
    def setUpTestData(cls):
        from clients.models import Product
        user = User.objects.create_user(username="ren_tabs", password="x")
        cls.emp = Employee.objects.create(user=user, role="admin", salary=0, active=True)
        cls.client_row = Client.objects.create(name="TAB CLIENT")

        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.life, _ = Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance"})
        # A sale names the exact plan; its parent is what the book is filed under.
        cls.life_plan, _ = Product.objects.get_or_create(
            code="PR_LIFE_PRO", defaults={"name": "PR Life Pro", "parent": cls.life})
        cls.life_plan.parent = cls.life
        cls.life_plan.save()

        today = date.today()
        cls.h = cls._renewal(cls.health, 1000, today)
        cls.l = cls._renewal(cls.life, 2000, today)
        cls.sub = cls._renewal(cls.life_plan, 3000, today)
        cls.other = cls._renewal(None, 500, today)
        # Falls due next week — the work queue, not this month's collection.
        cls.due_soon = cls._renewal(cls.health, 900, today, end=today + timedelta(days=7))
        cls.lapsed = cls._renewal(cls.health, 800, today, end=today - timedelta(days=5))

    @classmethod
    def _renewal(cls, product, amount, collected_on, end=None):
        return Renewal.objects.create(
            client=cls.client_row, employee=cls.emp, product_ref=product,
            product_type=Renewal.PRODUCT_TYPE_OTHER if product is None else "",
            product_name="Misc" if product is None else None,
            renewal_date=collected_on, renewal_end_date=end,
            premium_collected_on=collected_on,
            frequency=Renewal.FREQUENCY_YEARLY, premium_amount=amount,
        )

    def _get(self, **params):
        client = TestClient()
        client.force_login(self.emp.user)
        res = client.get(reverse("clients:all_renewals"), params)
        self.assertEqual(res.status_code, 200)
        return {r.id for r in res.context["renewals"]}, res.context

    def test_the_health_tab_excludes_life(self):
        ids, ctx = self._get(type="health")
        self.assertEqual(ctx["kind"], "health")
        self.assertEqual(ids, {self.h.id, self.due_soon.id, self.lapsed.id})

    def test_a_sub_product_renewal_files_under_its_parent_book(self):
        """A renewal names the exact plan ("PR Life Pro"); matching the code
        alone filed every plan-level renewal under Other."""
        ids, _ = self._get(type="life")
        self.assertIn(self.sub.id, ids)
        self.assertEqual(self.sub.insurance_kind, "life")

    def test_other_is_what_is_left(self):
        ids, _ = self._get(type="other")
        self.assertEqual(ids, {self.other.id})

    def test_the_tabs_count_every_book_not_just_the_open_one(self):
        _, ctx = self._get(type="health")
        counts = {t["key"]: t["count"] for t in ctx["tabs"]}
        self.assertEqual(counts["health"], 3)
        self.assertEqual(counts["life"], 2)     # main + sub-product plan
        self.assertEqual(counts["other"], 1)
        self.assertEqual(counts[""], 6)

    def test_a_search_keeps_the_open_book(self):
        _, ctx = self._get(type="life", q="TAB")
        self.assertEqual(ctx["kind"], "life")

    def test_due_soon_ignores_the_payment_window(self):
        ids, ctx = self._get(due="30")
        self.assertEqual(ctx["due"], "30")
        self.assertEqual(ctx["period"], "")
        self.assertEqual(ids, {self.due_soon.id})

    def test_overdue_lists_what_has_lapsed(self):
        ids, _ = self._get(due="overdue")
        self.assertEqual(ids, {self.lapsed.id})

    def test_due_and_book_combine(self):
        ids, _ = self._get(due="overdue", type="life")
        self.assertEqual(ids, set())

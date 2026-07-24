"""Multiyear health policies: only the first-year slice is this month's Fresh
business (the slab is read off that), and multiyear/EMI is Health-only."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from clients.models import Client, Employee, Product, Sale
from clients.views.reports import _month_margin_breakdown


class MultiyearSliceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.health.is_active = True
        cls.health.save()
        call_command("seed_health_slabs")
        u = User.objects.create_user("my_emp", password="x")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="MY Customer")

    def _sale(self, amount, years):
        today = timezone.localdate()
        return Sale.objects.create(
            client=self.customer, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal(amount), policy_type="fresh",
            policy_years=years, status=Sale.STATUS_APPROVED,
            date=today, policy_date=today, policy_number=f"P{amount}{years}",
        )

    def test_annual_premium_slice(self):
        s = self._sale("300000", 3)
        self.assertEqual(s.annual_premium, Decimal("100000"))
        self.assertTrue(s.is_multiyear)

    def test_report_counts_only_first_year_slice(self):
        # 3L over 3 years -> 1L Fresh this month -> slab for 1L is 27.5%.
        self._sale("300000", 3)
        today = timezone.localdate()
        rows, totals = _month_margin_breakdown(today.year, today.month)
        fresh = next(r for r in rows if r["product"] == "Health Insurance" and r["policy"] == "Fresh")
        self.assertEqual(fresh["revenue"], Decimal("100000.00"))
        self.assertEqual(fresh["margin_percent"], Decimal("27.50"))
        self.assertEqual(fresh["margin_amount"], Decimal("27500.00"))

    def test_single_year_unchanged(self):
        s = self._sale("100000", 1)
        self.assertEqual(s.annual_premium, Decimal("100000"))
        self.assertFalse(s.is_multiyear)


class MultiyearFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        Product.objects.get_or_create(code="SIP", defaults={"name": "SIP"})
        cls.customer = Client.objects.create(name="Form Customer")
        u = User.objects.create_user("form_emp", password="x")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)

    def _data(self, product, **extra):
        d = {"client": self.customer.id, "employee": self.emp.id, "product": product,
             "amount": "300000", "date": "2026-07-20", "policy_years": "3", "emi_months": "5"}
        d.update(extra)
        return d

    def test_health_keeps_multiyear_and_emi(self):
        from clients.forms import AdminSaleForm
        f = AdminSaleForm(data=self._data("Health Insurance", policy_type="fresh",
                                          policy_date="2026-07-20", policy_number="X1"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["policy_years"], 3)
        self.assertEqual(f.cleaned_data["emi_months"], 5)

    def test_non_health_forced_single_year(self):
        from clients.forms import AdminSaleForm
        f = AdminSaleForm(data=self._data("SIP"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["policy_years"], 1)
        self.assertEqual(f.cleaned_data["emi_months"], 0)

    def test_emi_requires_multiyear(self):
        from clients.forms import AdminSaleForm
        f = AdminSaleForm(data=self._data("Health Insurance", policy_years="1",
                                          policy_type="fresh", policy_date="2026-07-20",
                                          policy_number="X2"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["emi_months"], 0)  # EMI dropped without multiyear


class EmiWindowTests(TestCase):
    def test_window_starts_next_month(self):
        from clients.management.commands.emi_reminders import emi_window_contains

        class S:  # minimal stand-in
            emi_months = 5
            date = __import__("datetime").date(2026, 6, 10)
        s = S()
        self.assertFalse(emi_window_contains(s, 2026, 6))   # sale month — no
        self.assertTrue(emi_window_contains(s, 2026, 7))    # first EMI month
        self.assertTrue(emi_window_contains(s, 2026, 11))   # 5th (last)
        self.assertFalse(emi_window_contains(s, 2026, 12))  # past the window
        # crosses a year boundary: Nov sale + 5 months = Dec, Jan, Feb, Mar, Apr
        s.date = __import__("datetime").date(2026, 11, 3)
        self.assertFalse(emi_window_contains(s, 2026, 11))  # sale month
        self.assertTrue(emi_window_contains(s, 2026, 12))   # first EMI month
        self.assertTrue(emi_window_contains(s, 2027, 4))    # 5th (last)
        self.assertFalse(emi_window_contains(s, 2027, 5))   # past the window


class EmiReminderCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        seller = Employee.objects.create(
            user=User.objects.create_user("seller", password="x"), role="employee", salary=0, active=True)
        cls.owner = Employee.objects.create(
            user=User.objects.create_user("owner", password="x"), role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="EMI Client", mapped_to=cls.owner)
        cls.sale = Sale.objects.create(
            client=cls.customer, employee=seller, product="Health Insurance",
            product_ref=cls.health, amount=Decimal("300000"), policy_type="fresh",
            policy_years=3, emi_months=5, status=Sale.STATUS_APPROVED,
            date=__import__("datetime").date(2026, 6, 10),
            policy_date=__import__("datetime").date(2026, 6, 10), policy_number="EMI1",
        )

    def _run_on(self, d):
        import datetime
        from unittest import mock
        from django.core.management import call_command
        with mock.patch("clients.management.commands.emi_reminders.timezone.localdate",
                        return_value=datetime.date(*d)):
            call_command("emi_reminders")

    def test_task_assigned_to_mapped_owner_due_5th(self):
        from clients.models import Notification, Task
        self._run_on((2026, 8, 4))  # in window, day 4
        task = Task.objects.filter(assign_group="emi:%d:2026-08" % self.sale.id).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.assigned_to, self.owner)          # mapped employee, not seller
        self.assertEqual(task.due_date, __import__("datetime").date(2026, 8, 5))
        self.assertEqual(task.priority, Task.PRIORITY_HIGH)
        self.assertTrue(Notification.objects.filter(recipient=self.owner.user).exists())

    def test_no_duplicate_task_across_the_three_days(self):
        from clients.models import Task
        self._run_on((2026, 8, 3))
        self._run_on((2026, 8, 4))
        self._run_on((2026, 8, 5))
        self.assertEqual(Task.objects.filter(assign_group="emi:%d:2026-08" % self.sale.id).count(), 1)

    def test_noop_outside_window_and_off_days(self):
        from clients.models import Task
        self._run_on((2026, 6, 4))   # sale month — before window starts
        self._run_on((2026, 8, 10))  # in window but not a reminder day
        self.assertFalse(Task.objects.filter(assign_group__startswith="emi:").exists())


class MultiyearRenewalRecognitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        call_command("seed_health_slabs")  # sets renewal_margin_percent = 12.75
        emp = Employee.objects.create(
            user=User.objects.create_user("mr_emp", password="x"), role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="MR Client")
        import datetime
        cls.sale = Sale.objects.create(
            client=cls.customer, employee=emp, product="Health Insurance",
            product_ref=cls.health, amount=Decimal("300000"), policy_type="fresh",
            policy_years=3, status=Sale.STATUS_APPROVED,
            date=datetime.date(2026, 7, 15), policy_date=datetime.date(2026, 7, 15),
            policy_number="MR1",
        )

    def test_years_2_and_3_recognized_as_renewal(self):
        from clients.views.reports import _month_renewal_breakdown
        for yr in (2027, 2028):
            rows, _ = _month_renewal_breakdown(yr, 7)
            health = next(r for r in rows if r["product"] == "Health Insurance")
            self.assertEqual(health["revenue"], Decimal("100000"))
            self.assertEqual(health["margin_percent"], Decimal("12.75"))
            self.assertEqual(health["margin_amount"], Decimal("12750.00"))

    def test_year_1_is_not_a_renewal(self):
        from clients.views.reports import _month_renewal_breakdown
        rows, _ = _month_renewal_breakdown(2026, 7)  # sale/fresh month
        self.assertFalse(any(r["product"] == "Health Insurance" for r in rows))

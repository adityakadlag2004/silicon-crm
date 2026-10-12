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
             "subproduct": "Mediclaim" if product == "Health Insurance" else "",
             "amount": "300000", "date": "2026-07-20", "policy_years": "3", "emi_months": "5"}
        d.update(extra)
        return d

    def test_health_keeps_multiyear_and_emi(self):
        from clients.forms import AdminSaleForm
        f = AdminSaleForm(data=self._data("Health Insurance", policy_type="fresh",
                                          policy_date="2026-07-20", policy_number="X1", insurer="Star Health"))
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
                                          policy_number="X2", insurer="Star Health"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["emi_months"], 0)  # EMI dropped without multiyear

    def test_five_year_term_takes_a_23_month_emi(self):
        from clients.forms import AdminSaleForm
        f = AdminSaleForm(data=self._data("Health Insurance", policy_years="5", emi_months="23",
                                          policy_type="fresh", policy_date="2026-07-20",
                                          policy_number="X5", insurer="Star Health"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual((f.cleaned_data["policy_years"], f.cleaned_data["emi_months"]), (5, 23))

    def test_emi_tenure_is_capped_by_the_term(self):
        """2y up to 11 months, 3y up to 17, 4–5y up to 23 — the insurer's caps."""
        from clients.forms import AdminSaleForm
        for years, emi, ok in [("2", "11", True), ("2", "17", False), ("3", "17", True),
                               ("3", "23", False), ("4", "23", True)]:
            f = AdminSaleForm(data=self._data("Health Insurance", policy_years=years, emi_months=emi,
                                              policy_type="fresh", policy_date="2026-07-20",
                                              policy_number=f"C{years}{emi}", insurer="Star Health"))
            self.assertEqual(f.is_valid(), ok, (years, emi, f.errors))
            if not ok:
                self.assertIn("emi_months", f.errors)

    def test_personal_accident_is_sold_on_healths_terms(self):
        """PA is a Health sub-product (migration 0140): 1–5 years, EMI up to 23."""
        from clients.forms import AdminSaleForm
        f = AdminSaleForm(data=self._data("Health Insurance", subproduct="Personal Accident",
                                          policy_years="5", emi_months="23", policy_type="fresh",
                                          policy_date="2026-07-20", policy_number="PA5",
                                          insurer="Star Health"))
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["product"], "Personal Accident")
        self.assertEqual((f.cleaned_data["policy_years"], f.cleaned_data["emi_months"]), (5, 23))


class AppEmiCapTests(TestCase):
    """The app's Add Sale enforces the same term → EMI caps as the web form."""

    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        u = User.objects.create_user("emi_cap_admin", password="x")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.customer = Client.objects.create(name="EMI Cap Client")

    def _post(self, years, emi):
        import json
        from django.urls import reverse
        self.client.force_login(self.admin.user)
        return self.client.post(reverse("clients:app_sale_create"), content_type="application/json",
                                data=json.dumps({
                                    "client_id": self.customer.id,
                                    "product_id": Product.objects.get(code="HEALTH_MEDICLAIM").id,
                                    "amount": "250000", "policy_type": "fresh",
                                    "policy_date": "2026-07-20", "policy_number": f"APP{years}{emi}",
                                    "policy_years": years, "emi_months": emi}))

    def test_five_year_23_month_emi_is_booked(self):
        resp = self._post(5, 23)
        self.assertEqual(resp.status_code, 200, resp.content)
        sale = Sale.objects.get(policy_number="APP523")
        self.assertEqual((sale.policy_years, sale.emi_months), (5, 23))

    def test_over_the_cap_is_refused_not_dropped(self):
        resp = self._post(3, 23)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("17 months", resp.json()["error"])
        self.assertFalse(Sale.objects.filter(policy_number="APP323").exists())


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

    def test_a_cancelled_policy_is_not_chased_for_its_emis(self):
        """The policy is usually cancelled *because* the EMIs stopped. A monthly
        high-priority "call the client for the EMI" on a policy that no longer
        exists is the one task nobody should ever be assigned."""
        from clients.models import InsurancePolicy, Notification, Task

        InsurancePolicy.objects.create(
            client=self.customer, policy_number="EMI1", insurer="Starwell",
            insurance_type=InsurancePolicy.TYPE_HEALTH, source_sale=self.sale,
            status=InsurancePolicy.STATUS_CANCELLED)
        self._run_on((2026, 8, 4))
        self.assertFalse(Task.objects.filter(assign_group__startswith="emi:").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.owner.user).exists())

    def test_a_live_policy_is_still_chased(self):
        from clients.models import InsurancePolicy, Task

        InsurancePolicy.objects.create(
            client=self.customer, policy_number="EMI1", insurer="Starwell",
            insurance_type=InsurancePolicy.TYPE_HEALTH, source_sale=self.sale,
            status=InsurancePolicy.STATUS_ACTIVE)
        self._run_on((2026, 8, 4))
        self.assertTrue(Task.objects.filter(assign_group__startswith="emi:").exists())

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

    def test_a_health_plan_sub_product_rolls_up(self):
        """A multiyear sale naming a specific health plan is still health
        business. `_is_health_product` already pays the employee for years
        2..N of it, so matching the parent code alone here would credit points
        for a year the margin report never recognised."""
        import datetime
        from clients.views.reports import _month_renewal_breakdown

        plan = Product.objects.create(code="HEALTH_PLUS", name="Health Plus",
                                      parent=self.health)
        Sale.objects.create(
            client=self.customer, employee=self.sale.employee, product=plan.name,
            product_ref=plan, amount=Decimal("150000"), policy_type="fresh",
            policy_years=3, status=Sale.STATUS_APPROVED,
            date=datetime.date(2026, 7, 15), policy_date=datetime.date(2026, 7, 15),
            policy_number="MR2",
        )
        rows, _ = _month_renewal_breakdown(2027, 7)
        health = next(r for r in rows if r["product"] == "Health Insurance")
        self.assertEqual(health["revenue"], Decimal("150000"))   # 100,000 + 50,000

    def test_year_1_is_not_a_renewal(self):
        from clients.views.reports import _month_renewal_breakdown
        rows, _ = _month_renewal_breakdown(2026, 7)  # sale/fresh month
        self.assertFalse(any(r["product"] == "Health Insurance" for r in rows))

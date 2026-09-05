"""Future points: the multiyear years that have not landed yet, and cancelling.

Two things worth pinning. The statement has to add up — a total that disagrees
with the FY lines it is made of is worse than no page. And a cancelled policy
has to stop paying: a 3-year premium is credited one year at a time, so when
the policy goes the remaining years must never arrive.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from clients.models import (Client, Employee, IncentiveAccrual, InsurancePolicy,
                            Product, Sale)
from clients.services import incentives as inc


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        call_command("seed_incentive_structure")
        cls.user = User.objects.create_user("fp_seller", password="x")
        cls.emp = Employee.objects.create(user=cls.user, role="employee")
        cls.admin_user = User.objects.create_superuser("fp_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin")
        cls.customer = Client.objects.create(name="FP Client")

    def sell(self, amount=90000, years=3, start=date(2026, 6, 10), number="FPPOL1"):
        """A multiyear health policy with its tracker row, as approval creates it."""
        sale = Sale.objects.create(
            client=self.customer, employee=self.emp, product=self.health.name,
            product_ref=self.health, amount=Decimal(str(amount)), date=start,
            status=Sale.STATUS_APPROVED, policy_type="fresh",
            policy_years=years, policy_date=start, policy_number=number)
        policy = InsurancePolicy.objects.create(
            client=self.customer, policy_number=number, insurer="Starwell",
            insurance_type=InsurancePolicy.TYPE_HEALTH, source_sale=sale,
            premium_amount=Decimal(str(amount)), start_date=start)
        return sale, policy


class StatementTests(_Base):
    def test_years_and_months_add_up_to_the_total(self):
        self.sell()  # 30,000 a year: 2027-06-10 and 2028-06-10
        data = inc.future_points(self.emp)
        self.assertEqual([y["label"] for y in data["years"]], ["FY 27-28", "FY 28-29"])
        self.assertEqual([m["date"] for y in data["years"] for m in y["months"]],
                         [date(2027, 6, 1), date(2028, 6, 1)])
        self.assertEqual(data["count"], 2)
        self.assertEqual(sum((y["points"] for y in data["years"]), Decimal("0")),
                         data["total"])
        self.assertEqual(data["total"], Decimal("1200.000"))  # 2 × 2.00% of 30,000

    def test_a_row_names_the_policy_and_the_client(self):
        _sale, policy = self.sell()
        row = inc.pending_accruals(self.emp)[0]
        self.assertEqual(row["policy"], policy)
        self.assertEqual(row["client"], self.customer)
        self.assertEqual(row["policy_number"], "FPPOL1")

    def test_no_employee_reads_the_whole_firm(self):
        self.sell()
        self.assertEqual(inc.future_points()["count"], 2)

    def test_a_year_already_credited_drops_off(self):
        sale, _p = self.sell(start=date(2025, 6, 10))
        inc.issue_due_accruals(sale, on_date=date(2026, 6, 10))
        self.assertEqual([r["year_index"] for r in inc.pending_accruals(self.emp)], [3])


class CancellationTests(_Base):
    def test_cancelling_the_policy_stops_the_remaining_years(self):
        sale, policy = self.sell()
        policy.status = InsurancePolicy.STATUS_CANCELLED
        policy.save()
        self.assertEqual(inc.accrual_schedule(sale), [])
        self.assertEqual(inc.future_points(self.emp)["total"], Decimal("0"))

    def test_a_cancelled_policy_is_never_credited_by_the_cron(self):
        sale, policy = self.sell(start=date(2025, 6, 10))
        policy.status = InsurancePolicy.STATUS_CANCELLED
        policy.save()
        self.assertEqual(inc.issue_due_accruals(sale, on_date=date(2028, 1, 1)), [])
        call_command("multiyear_incentive_accruals")
        self.assertFalse(IncentiveAccrual.objects.filter(sale=sale).exists())

    def test_year_one_points_survive_the_cancellation(self):
        """The first year was sold, delivered and paid for — only what has not
        landed yet is taken back."""
        sale, policy = self.sell()
        policy.status = InsurancePolicy.STATUS_CANCELLED
        policy.save()
        sale.refresh_from_db()
        self.assertEqual(sale.points, Decimal("600.000"))

    def test_reinstating_brings_the_remaining_years_back(self):
        sale, policy = self.sell()
        policy.status = InsurancePolicy.STATUS_CANCELLED
        policy.save()
        policy.status = InsurancePolicy.STATUS_ACTIVE
        policy.save()
        self.assertEqual(len(inc.accrual_schedule(sale)), 2)

    def test_a_sale_with_no_tracker_policy_still_accrues(self):
        sale, policy = self.sell()
        policy.delete()
        sale = Sale.objects.get(pk=sale.pk)
        self.assertEqual(len(inc.accrual_schedule(sale)), 2)


class PageTests(_Base):
    def test_employee_sees_their_own_statement(self):
        self.sell()
        self.client.force_login(self.user)
        html = self.client.get(reverse("clients:future_points")).content.decode()
        self.assertIn("FPPOL1", html)
        self.assertIn("FY 27-28", html)
        self.assertIn("Jun 2027", html)

    def test_the_seller_can_cancel_their_own_policy(self):
        """The employee is the one told the EMIs stopped, and the only points a
        cancellation takes away are theirs."""
        _sale, policy = self.sell()
        self.client.force_login(self.user)
        self.assertIn("Cancel policy FPPOL1",
                      self.client.get(reverse("clients:future_points")).content.decode())
        self.client.post(reverse("clients:policy_cancel", args=[policy.id]),
                         {"status": "cancelled", "reason": "EMI not paid — policy cancelled"})
        policy.refresh_from_db()
        self.assertEqual(policy.status, InsurancePolicy.STATUS_CANCELLED)
        self.assertIn("EMI not paid", policy.notes)

    def test_admin_can_cancel_from_the_page(self):
        _sale, policy = self.sell()
        self.client.force_login(self.admin_user)
        url = reverse("clients:future_points")
        self.assertIn("Cancel policy", self.client.get(url).content.decode())
        self.client.post(reverse("clients:policy_cancel", args=[policy.id]),
                         {"status": "cancelled", "reason": "client left", "next": url})
        policy.refresh_from_db()
        self.assertEqual(policy.status, InsurancePolicy.STATUS_CANCELLED)
        self.assertIn("client left", policy.notes)
        # (the policy number still shows in the success message, so check the statement)
        self.assertIn("Nothing pending", self.client.get(url).content.decode())

    def test_someone_elses_policy_is_not_theirs_to_cancel(self):
        _sale, policy = self.sell()
        other = User.objects.create_user("fp_other", password="x")
        Employee.objects.create(user=other, role="employee")
        self.client.force_login(other)
        self.client.post(reverse("clients:policy_cancel", args=[policy.id]),
                         {"status": "cancelled"})
        policy.refresh_from_db()
        self.assertEqual(policy.status, InsurancePolicy.STATUS_ACTIVE)

    def test_the_months_are_their_own_dropdowns(self):
        """FY opens into months, a month opens into the policies inside it."""
        self.sell()
        self.sell(amount=120000, years=2, start=date(2026, 9, 1), number="FPPOL2")
        self.client.force_login(self.user)
        html = self.client.get(reverse("clients:future_points")).content.decode()
        self.assertEqual(html.count('<details class="fp-year"'), 2)   # FY 27-28, FY 28-29
        self.assertEqual(html.count('<details class="fp-month"'), 3)  # Jun/Sep 27, Jun 28

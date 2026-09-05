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

    def test_any_non_live_status_stops_the_remaining_years(self):
        """Cancelled is not the only way a policy dies. "Lapsed" is what an
        EMI-killed policy is often marked, and matured means the term is over —
        none of them should keep paying. Nothing sets these automatically, so
        every one of them is somebody's deliberate act."""
        for status in (InsurancePolicy.STATUS_LAPSED, InsurancePolicy.STATUS_MATURED,
                       InsurancePolicy.STATUS_CANCELLED):
            with self.subTest(status=status):
                sale, policy = self.sell(number=f"FPST{status[:4]}")
                policy.status = status
                policy.save()
                self.assertEqual(inc.accrual_schedule(sale), [])

    def test_a_policy_linked_only_by_number_still_stops_the_years(self):
        """A policy back-filled from a renewal carries no source_sale. Reading
        the link alone left exactly those policies unable to stop anything."""
        sale, policy = self.sell(number="FPBYNUM")
        InsurancePolicy.objects.filter(pk=policy.pk).update(source_sale=None)
        sale = Sale.objects.get(pk=sale.pk)
        self.assertEqual(len(inc.accrual_schedule(sale)), 2)

        policy.refresh_from_db()
        policy.status = InsurancePolicy.STATUS_CANCELLED
        policy.save()
        sale = Sale.objects.get(pk=sale.pk)
        self.assertEqual(inc.accrual_schedule(sale), [])

    def test_the_row_finds_a_policy_matched_by_number(self):
        sale, policy = self.sell(number="FPBYNUM2")
        InsurancePolicy.objects.filter(pk=policy.pk).update(source_sale=None)
        row = inc.pending_accruals(self.emp)[0]
        self.assertEqual(row["policy"], policy)

    def test_a_number_matched_policy_is_the_sellers_to_cancel(self):
        _sale, policy = self.sell(number="FPBYNUM3")
        InsurancePolicy.objects.filter(pk=policy.pk).update(source_sale=None)
        self.client.force_login(self.user)
        self.client.post(reverse("clients:policy_cancel", args=[policy.id]),
                         {"status": "cancelled"})
        policy.refresh_from_db()
        self.assertEqual(policy.status, InsurancePolicy.STATUS_CANCELLED)

    def test_a_policy_number_belonging_to_another_client_is_not_this_policy(self):
        """The number is only an identity within a client — two clients can
        carry the same string, and matching globally would let one client's
        cancellation silence another's points."""
        sale, policy = self.sell(number="SHARED")
        InsurancePolicy.objects.filter(pk=policy.pk).update(source_sale=None)
        other = Client.objects.create(name="Someone Else")
        InsurancePolicy.objects.create(
            client=other, policy_number="SHARED", insurer="X",
            insurance_type=InsurancePolicy.TYPE_HEALTH,
            status=InsurancePolicy.STATUS_CANCELLED)
        sale = Sale.objects.get(pk=sale.pk)
        self.assertEqual(len(inc.accrual_schedule(sale)), 2)

    def test_pricing_the_book_does_not_query_per_sale(self):
        """The admin view reads every multiyear sale in the firm. The rule and
        its slabs are the same for all of them, so they are read once."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        def cost():
            with CaptureQueriesContext(connection) as q:
                inc.future_points()
            return len(q)

        for n in range(4):
            self.sell(number=f"FPQ{n}")
        four = cost()
        for n in range(4, 12):
            self.sell(number=f"FPQ{n}")
        self.assertEqual(cost(), four)   # 8 more sales, not one more query

    def test_a_sale_with_no_tracker_policy_still_accrues(self):
        sale, policy = self.sell()
        policy.delete()
        sale = Sale.objects.get(pk=sale.pk)
        self.assertEqual(len(inc.accrual_schedule(sale)), 2)


class TermBoundaryTests(_Base):
    """A policy is sold for a term and pays for that term — nothing more.

    Two years sold, two years paid; three sold, three paid. What happens after
    the term is a *renewal*, which is entered as a Renewal and carries no
    points at all, so nothing here may keep crediting once the term is up.
    """

    def _credits(self, sale, years=12):
        """Every credit the engine would ever issue, running the job monthly
        for `years` years — the real defence against "extra points continuously"
        is that re-running it forever changes nothing."""
        from datetime import timedelta

        day = sale.policy_date
        end = date(sale.policy_date.year + years, 1, 1)
        while day < end:
            inc.issue_due_accruals(sale, on_date=day)
            day += timedelta(days=15)
        return list(IncentiveAccrual.objects.filter(sale=sale).order_by("year_index"))

    def test_a_three_year_policy_pays_exactly_three_years(self):
        sale, _p = self.sell(amount=90000, years=3, start=date(2026, 6, 10))
        credits = self._credits(sale)
        self.assertEqual([c.year_index for c in credits], [2, 3])   # year 1 came with the sale
        self.assertEqual(sale.points, Decimal("600.000"))
        self.assertEqual(sum((c.points for c in credits), Decimal("0")), Decimal("1200.000"))
        # The whole premium is credited exactly once, in three slices: the
        # year-1 slice at sale time plus one slice per later year.
        self.assertEqual(sale.annual_premium + sum((c.amount for c in credits), Decimal("0")),
                         sale.amount)

    def test_a_two_year_policy_pays_exactly_two_years(self):
        sale, _p = self.sell(amount=60000, years=2, start=date(2026, 6, 10))
        self.assertEqual([c.year_index for c in self._credits(sale)], [2])

    def test_a_single_year_policy_pays_once_and_never_again(self):
        sale = Sale.objects.create(
            client=self.customer, employee=self.emp, product=self.health.name,
            product_ref=self.health, amount=Decimal("30000"), date=date(2026, 6, 10),
            status=Sale.STATUS_APPROVED, policy_type="fresh", policy_years=1,
            policy_date=date(2026, 6, 10), policy_number="FPONE")
        self.assertEqual(self._credits(sale), [])

    def test_nothing_is_ever_credited_after_the_term_ends(self):
        sale, _p = self.sell(amount=90000, years=3, start=date(2026, 6, 10))
        credits = self._credits(sale)
        self.assertEqual(sale.coverage_end(), date(2029, 6, 10))
        self.assertTrue(all(c.due_date < sale.coverage_end() for c in credits))

    def test_the_renewal_after_the_term_earns_no_points(self):
        """The client renews when the term ends; that renewal is a Renewal row,
        and a Renewal carries no points — the employee is paid on the sale."""
        from clients.models import Renewal

        sale, policy = self.sell(amount=90000, years=3, start=date(2026, 6, 10))
        self._credits(sale)
        before = IncentiveAccrual.objects.filter(sale=sale).count()
        Renewal.objects.create(
            client=self.customer, employee=self.emp, policy=policy,
            product_ref=self.health, premium_amount=Decimal("32000"),
            renewal_date=date(2029, 6, 10), premium_collected_on=date(2029, 6, 5))
        self._credits(sale, years=14)
        self.assertEqual(IncentiveAccrual.objects.filter(sale=sale).count(), before)
        self.assertFalse(hasattr(Renewal, "points"))

    def test_the_job_is_idempotent_however_often_it_runs(self):
        sale, _p = self.sell(amount=90000, years=3, start=date(2026, 6, 10))
        self._credits(sale)
        first = list(IncentiveAccrual.objects.filter(sale=sale).values_list("id", "points"))
        self._credits(sale)          # run the whole decade again
        self.assertEqual(
            list(IncentiveAccrual.objects.filter(sale=sale).values_list("id", "points")), first)


class PageTests(_Base):
    def test_employee_sees_their_own_statement(self):
        self.sell()
        self.client.force_login(self.user)
        html = self.client.get(reverse("clients:future_points")).content.decode()
        self.assertIn("FPPOL1", html)
        self.assertIn("FY 27-28", html)
        self.assertIn("Jun 2027", html)

    def test_an_employee_never_sees_anyone_elses_points(self):
        """The page is a personal statement: no picker, and nothing on it that
        belongs to somebody else."""
        self.sell()
        other = User.objects.create_user("fp_other_seller", password="x")
        other_emp = Employee.objects.create(user=other, role="employee")
        sale, _p = self.sell(number="FPOTHER")
        Sale.objects.filter(pk=sale.pk).update(employee=other_emp)

        self.client.force_login(self.user)
        html = self.client.get(reverse("clients:future_points")).content.decode()
        self.assertIn("FPPOL1", html)
        self.assertNotIn("FPOTHER", html)
        self.assertNotIn("Everyone", html)          # no employee picker
        self.assertEqual(inc.future_points(self.emp)["count"], 2)   # their two years only

    def test_an_employee_cannot_read_another_persons_statement_by_url(self):
        self.sell()
        other = User.objects.create_user("fp_nosy", password="x")
        Employee.objects.create(user=other, role="employee")
        self.client.force_login(other)
        html = self.client.get(
            reverse("clients:future_points") + f"?employee={self.emp.id}").content.decode()
        self.assertNotIn("FPPOL1", html)

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

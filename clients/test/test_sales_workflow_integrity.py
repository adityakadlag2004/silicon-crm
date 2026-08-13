"""Regression tests for the sale-approval workflow controls and the
employee/team data-protection rules.

Run: venv_new/bin/python manage.py test clients.test.test_sales_workflow_integrity -v 2
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import (
    AuditLog,
    Client,
    Employee,
    IncentiveRule,
    IncentiveSlab,
    Lead,
    Sale,
)


class _WorkflowSetup(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        cls.emps = {}
        for role in ("admin", "employee"):
            user = User.objects.create_user(
                username=f"wf_{role}", password="testpass123", email=f"{role}@wf.local"
            )
            cls.users[role] = user
            cls.emps[role] = Employee.objects.create(user=user, role=role, salary=0, active=True)
        cls.other_emp_user = User.objects.create_user(username="wf_other", password="testpass123")
        cls.other_emp = Employee.objects.create(user=cls.other_emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Test Customer", phone="9999999999")

    def setUp(self):
        self.http = {}
        for role, user in self.users.items():
            c = TestClient()
            c.force_login(user)
            self.http[role] = c

    def _make_sale(self, employee, status=Sale.STATUS_PENDING, amount="1000"):
        sale = Sale.objects.create(
            client=self.customer,
            employee=employee,
            product="SIP",
            amount=Decimal(amount),
            status=status,
        )
        return sale


class SaleAttributionTests(_WorkflowSetup):
    def test_employee_can_log_sale_under_a_colleague(self):
        # Anyone may credit a sale to a colleague — but it still pends, so this
        # is never a route to approving your own or anyone else's business.
        resp = self.http["employee"].post(reverse("clients:add_sale"), {
            "client": self.customer.id,
            "employee": self.other_emp.id,
            "product": "SIP",
            "amount": "5000",
            "date": "2026-07-01",
        })
        self.assertEqual(resp.status_code, 302)
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.employee, self.other_emp)
        self.assertEqual(sale.status, Sale.STATUS_PENDING)

    def test_employee_can_add_sale_without_employee_field(self):
        # An omitted employee field must still save under the logged-in
        # employee (regression: form treated employee as required → 200, no save).
        before = Sale.objects.count()
        resp = self.http["employee"].post(reverse("clients:add_sale"), {
            "client": self.customer.id,
            "product": "SIP",
            "amount": "5000",
            "date": "2026-07-01",
        })
        self.assertEqual(resp.status_code, 302, "employee sale POST should redirect (save), not re-render")
        self.assertEqual(Sale.objects.count(), before + 1)
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.employee, self.emps["employee"])
        self.assertEqual(sale.status, Sale.STATUS_PENDING)

    def test_admin_can_attribute_sale_to_other_employee(self):
        resp = self.http["admin"].post(reverse("clients:add_sale"), {
            "client": self.customer.id,
            "employee": self.other_emp.id,
            "product": "SIP",
            "amount": "5000",
            "date": "2026-07-01",
        })
        self.assertEqual(resp.status_code, 302)
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.employee, self.other_emp)
        self.assertEqual(sale.status, Sale.STATUS_APPROVED)  # admin sales auto-approve


class SaleEditDeleteTests(_WorkflowSetup):
    def test_employee_edit_of_approved_sale_resets_to_pending(self):
        sale = self._make_sale(self.emps["employee"], status=Sale.STATUS_APPROVED)
        resp = self.http["employee"].post(
            reverse("clients:edit_sale", args=[sale.id]),
            {"product": "SIP", "amount": "9999", "date": "2026-07-01"},
        )
        self.assertEqual(resp.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_PENDING)
        self.assertIsNone(sale.approved_by)
        self.assertIsNone(sale.approved_at)

    def test_admin_edit_keeps_approved_status(self):
        sale = self._make_sale(self.emps["employee"], status=Sale.STATUS_APPROVED)
        resp = self.http["admin"].post(
            reverse("clients:edit_sale", args=[sale.id]),
            {"product": "SIP", "amount": "7777", "date": "2026-07-01"},
        )
        self.assertEqual(resp.status_code, 302)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_APPROVED)

    def test_employee_cannot_delete_approved_sale(self):
        sale = self._make_sale(self.emps["employee"], status=Sale.STATUS_APPROVED)
        resp = self.http["employee"].post(reverse("clients:delete_sale", args=[sale.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

    def test_employee_can_delete_own_pending_sale(self):
        sale = self._make_sale(self.emps["employee"], status=Sale.STATUS_PENDING)
        resp = self.http["employee"].post(reverse("clients:delete_sale", args=[sale.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())


class RejectedSaleAccountingTests(_WorkflowSetup):
    def test_rejected_sale_earns_zero_points(self):
        IncentiveRule.objects.create(product="SIP", unit_amount=Decimal("1000"), points_per_unit=Decimal("1"))
        sale = self._make_sale(self.emps["employee"], status=Sale.STATUS_APPROVED, amount="5000")
        self.assertGreater(sale.points, 0)
        sale.status = Sale.STATUS_REJECTED
        sale.save()
        sale.refresh_from_db()
        self.assertEqual(sale.points, Decimal("0.000"))

    def test_rejected_sales_do_not_count_toward_slab_cumulative(self):
        rule = IncentiveRule.objects.create(
            product="Life Insurance", unit_amount=Decimal("1000"), points_per_unit=Decimal("0")
        )
        IncentiveSlab.objects.create(rule=rule, threshold=Decimal("10000"), payout=Decimal("500"))

        # A rejected 8k sale must not help this 3k sale cross the 10k slab.
        rejected = Sale.objects.create(
            client=self.customer, employee=self.emps["employee"],
            product="Life Insurance", amount=Decimal("8000"), status=Sale.STATUS_REJECTED,
        )
        small = Sale.objects.create(
            client=self.customer, employee=self.emps["employee"],
            product="Life Insurance", amount=Decimal("3000"), status=Sale.STATUS_APPROVED,
        )
        small.refresh_from_db()
        self.assertEqual(small.points, Decimal("0.000"))

    def test_rejected_sale_does_not_set_client_product_status(self):
        Sale.objects.create(
            client=self.customer, employee=self.emps["employee"],
            product="SIP", amount=Decimal("5000"), status=Sale.STATUS_REJECTED,
        )
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.sip_status)


class EmployeeDeletionProtectionTests(_WorkflowSetup):
    def test_team_delete_refused_when_employee_has_sales(self):
        self._make_sale(self.other_emp)
        resp = self.http["admin"].post(reverse("clients:team_delete", args=[self.other_emp.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Employee.objects.filter(pk=self.other_emp.pk).exists())
        self.assertTrue(Sale.objects.filter(employee=self.other_emp).exists())

    def test_sale_employee_fk_is_protected(self):
        self._make_sale(self.other_emp)
        with self.assertRaises(ProtectedError):
            self.other_emp.delete()

    def test_lead_assigned_to_fk_is_protected(self):
        Lead.objects.create(customer_name="Lead X", assigned_to=self.other_emp)
        with self.assertRaises(ProtectedError):
            self.other_emp.delete()

    def test_team_delete_works_for_employee_without_history(self):
        clean_user = User.objects.create_user(username="wf_clean", password="testpass123")
        clean_emp = Employee.objects.create(user=clean_user, role="employee", salary=0, active=True)
        resp = self.http["admin"].post(reverse("clients:team_delete", args=[clean_emp.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Employee.objects.filter(pk=clean_emp.pk).exists())


class TeamEditValidationTests(_WorkflowSetup):
    def test_bogus_role_rejected(self):
        resp = self.http["admin"].post(
            reverse("clients:team_edit", args=[self.other_emp.id]), {"role": "superboss"}
        )
        self.other_emp.refresh_from_db()
        self.assertEqual(self.other_emp.role, "employee")

    def test_bad_salary_rejected(self):
        resp = self.http["admin"].post(
            reverse("clients:team_edit", args=[self.other_emp.id]),
            {"role": "employee", "salary": "not-a-number"},
        )
        self.other_emp.refresh_from_db()
        self.assertEqual(self.other_emp.salary, 0)

    def test_duplicate_employee_number_rejected(self):
        self.emps["employee"].employee_number = "EMP042"
        self.emps["employee"].save(update_fields=["employee_number"])
        resp = self.http["admin"].post(
            reverse("clients:team_edit", args=[self.other_emp.id]),
            {"role": "employee", "employee_number": "EMP042"},
        )
        self.other_emp.refresh_from_db()
        self.assertNotEqual(self.other_emp.employee_number, "EMP042")

    def test_role_change_writes_audit_log(self):
        self.http["admin"].post(
            reverse("clients:team_edit", args=[self.other_emp.id]), {"role": "manager"}
        )
        self.other_emp.refresh_from_db()
        self.assertEqual(self.other_emp.role, "manager")
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.ACTION_EMPLOYEE_ROLE_CHANGED,
                target_model="Employee",
                target_id=self.other_emp.pk,
            ).exists()
        )


class RecalcPointsTests(_WorkflowSetup):
    def test_get_not_allowed(self):
        resp = self.http["admin"].get(reverse("clients:recalc_points"))
        self.assertEqual(resp.status_code, 405)

    def test_employee_forbidden(self):
        resp = self.http["employee"].post(reverse("clients:recalc_points"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_recalc(self):
        self._make_sale(self.emps["employee"])
        resp = self.http["admin"].post(reverse("clients:recalc_points"))
        self.assertEqual(resp.status_code, 302)


class SaleDateFilterTests(_WorkflowSetup):
    """A single date bound must filter on its own — backdated entries were
    invisible because ?start_date= without an end date was silently ignored."""

    def _dated_sale(self, on, status=Sale.STATUS_PENDING):
        sale = self._make_sale(self.emps["employee"], status=status)
        Sale.objects.filter(pk=sale.pk).update(date=on)
        return sale

    def test_all_sales_start_date_alone_filters(self):
        from datetime import date, timedelta

        old = self._dated_sale(date.today() - timedelta(days=30))
        recent = self._dated_sale(date.today())
        resp = self.http["admin"].get(
            reverse("clients:all_sales"),
            {"start_date": (date.today() - timedelta(days=5)).isoformat()},
        )
        ids = [s.id for s in resp.context["sales"]]
        self.assertIn(recent.id, ids)
        self.assertNotIn(old.id, ids)

    def test_all_sales_end_date_alone_finds_backdated(self):
        from datetime import date, timedelta

        old = self._dated_sale(date.today() - timedelta(days=30))
        recent = self._dated_sale(date.today())
        resp = self.http["admin"].get(
            reverse("clients:all_sales"),
            {"end_date": (date.today() - timedelta(days=5)).isoformat()},
        )
        ids = [s.id for s in resp.context["sales"]]
        self.assertIn(old.id, ids)
        self.assertNotIn(recent.id, ids)

    def test_approve_sales_start_date_alone_filters(self):
        from datetime import date, timedelta

        old = self._dated_sale(date.today() - timedelta(days=30))
        recent = self._dated_sale(date.today())
        resp = self.http["admin"].get(
            reverse("clients:approve_sales"),
            {"start_date": (date.today() - timedelta(days=5)).isoformat()},
        )
        ids = [s.id for s in resp.context["sales"]]
        self.assertIn(recent.id, ids)
        self.assertNotIn(old.id, ids)

    def test_all_sales_defaults_to_this_month_not_today(self):
        """Unfiltered, the page used to show only *today's* sales, so it opened
        blank on any day nobody had booked yet."""
        from datetime import date, timedelta

        today = date.today()
        first = today.replace(day=1)
        earlier_this_month = self._dated_sale(first)
        last_month = self._dated_sale(first - timedelta(days=1))

        resp = self.http["admin"].get(reverse("clients:all_sales"))
        ids = [s.id for s in resp.context["sales"]]
        self.assertIn(earlier_this_month.id, ids)
        self.assertNotIn(last_month.id, ids)
        self.assertEqual(resp.context["start_date"], first)
        self.assertEqual(resp.context["end_date"], today)
        self.assertTrue(resp.context["default_range"])

    def test_all_sales_explicit_dates_beat_the_month_default(self):
        from datetime import date, timedelta

        old = self._dated_sale(date.today() - timedelta(days=200))
        resp = self.http["admin"].get(
            reverse("clients:all_sales"),
            {"start_date": (date.today() - timedelta(days=365)).isoformat()},
        )
        self.assertIn(old.id, [s.id for s in resp.context["sales"]])
        self.assertFalse(resp.context["default_range"])


class AddSaleRejectionTests(_WorkflowSetup):
    """A rejected Add Sale used to look exactly like a page reload: no message,
    no error, the picked client gone, and nothing in the approval queue."""

    def _post(self, **over):
        from datetime import date

        data = {
            "client": str(self.customer.id),
            "employee": str(self.emps["employee"].id),
            "product": "Life Insurance",
            "amount": "50000",
            "cover_amount": "",
            "date": date.today().isoformat(),
            "policy_date": date.today().isoformat(),
            "policy_number": "LI-REJ-1",
            "policy_years": "1",
            "emi_months": "0",
        }
        data.update(over)
        return self.http["employee"].post(reverse("clients:add_sale"), data)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from clients.models import Product

        Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance", "is_active": True})

    def test_missing_field_shows_why_and_keeps_the_client(self):
        resp = self._post(date="")
        body = resp.content.decode()
        self.assertIn("This sale was not saved", body)
        self.assertIn("This field is required.", body)
        self.assertIn(f'id="client-id" value="{self.customer.id}"', body)
        self.assertFalse(Sale.objects.filter(policy_number="LI-REJ-1").exists())

    def test_amount_written_with_commas_is_accepted(self):
        resp = self._post(amount="1,50,000", cover_amount="10,00,000")
        self.assertEqual(resp.status_code, 302)
        sale = Sale.objects.get(policy_number="LI-REJ-1")
        self.assertEqual(sale.amount, Decimal("150000"))
        self.assertEqual(sale.cover_amount, Decimal("1000000"))
        self.assertEqual(sale.status, "pending")

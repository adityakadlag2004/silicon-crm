"""Tests for the extracted services: sales workflow + targets month-close.

The sales service is the single implementation behind both the web views and
the app JSON API; these tests pin its behavior directly. close_month gained a
real fix in the consolidation: it now resolves per-employee targets
(EmployeeTarget override, else baseline) exactly like the dashboards do —
the old command only ever used the global baseline.

Run: .venv/bin/python manage.py test clients.test.test_services_extraction
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from clients.models import (
    Client,
    Employee,
    EmployeeTarget,
    MonthlyTargetHistory,
    Product,
    Sale,
    Target,
)
from clients.services import sales as sales_service
from clients.services import targets as targets_service


def _mk_emp(username, role="employee"):
    user = User.objects.create_user(username=username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


class SalesServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk_emp("svc_admin", "admin")
        cls.emp = _mk_emp("svc_emp")
        cls.client_row = Client.objects.create(name="Svc Client")
        cls.product, _ = Product.objects.get_or_create(name="PMS", defaults={"code": "PMS"})

    def _new_sale(self, employee):
        return Sale(
            client=self.client_row, employee=employee,
            product=self.product.name, amount=Decimal("100000"),
        )

    def test_finalize_auto_approve_sets_review_fields(self):
        sale = self._new_sale(self.admin)
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_APPROVED)
        self.assertEqual(sale.approved_by, self.admin.user)
        self.assertIsNotNone(sale.approved_at)
        self.assertEqual(sale.product_ref, self.product)  # resolved from name

    def test_finalize_pending_for_non_admin(self):
        sale = self._new_sale(self.emp)
        sales_service.finalize_new_sale(sale, self.emp.user, auto_approve=False)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_PENDING)
        self.assertIsNone(sale.approved_by)
        self.assertIsNone(sale.approved_at)

    def test_approve_and_reject(self):
        sale = self._new_sale(self.emp)
        sales_service.finalize_new_sale(sale, self.emp.user, auto_approve=False)

        sales_service.approve_sale(sale, self.admin.user)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_APPROVED)
        self.assertEqual(sale.approved_by, self.admin.user)

        sales_service.reject_sale(sale, self.admin.user, "  wrong amount  ")
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.STATUS_REJECTED)
        self.assertEqual(sale.rejection_reason, "wrong amount")

    def test_delete_removes_row(self):
        sale = self._new_sale(self.emp)
        sales_service.finalize_new_sale(sale, self.emp.user, auto_approve=False)
        pk = sale.pk
        sales_service.delete_sale(sale, self.admin.user)
        self.assertFalse(Sale.objects.filter(pk=pk).exists())


class CloseMonthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = _mk_emp("cm_emp")
        cls.other = _mk_emp("cm_other")
        cls.client_row = Client.objects.create(name="CM Client")
        cls.product, _ = Product.objects.get_or_create(name="SIP", defaults={"code": "SIP"})
        Target.objects.create(product="SIP", target_type="monthly", target_value=Decimal("50000"))
        # cm_emp carries a personal override — the dashboards honor it and,
        # post-consolidation, so must the recorded history
        EmployeeTarget.objects.create(employee=cls.emp, product="SIP", target_value=Decimal("80000"))
        Sale.objects.create(
            client=cls.client_row, employee=cls.emp, product="SIP",
            product_ref=cls.product, amount=Decimal("60000"),
            date="2026-06-15", status=Sale.STATUS_APPROVED,
        )

    def test_close_month_uses_employee_override(self):
        targets_service.close_month(2026, 6)
        row = MonthlyTargetHistory.objects.get(employee=self.emp, product="SIP", year=2026, month=6)
        self.assertEqual(row.target_value, Decimal("80000"))  # override, not baseline
        self.assertEqual(row.achieved_value, Decimal("60000"))

    def test_close_month_baseline_for_others(self):
        targets_service.close_month(2026, 6)
        row = MonthlyTargetHistory.objects.get(employee=self.other, product="SIP", year=2026, month=6)
        self.assertEqual(row.target_value, Decimal("50000"))
        self.assertEqual(row.achieved_value, Decimal("0"))

    def test_close_month_records_points(self):
        targets_service.close_month(2026, 6)
        row = MonthlyTargetHistory.objects.get(employee=self.emp, product="SIP", year=2026, month=6)
        # points were computed by Sale.save(); history must carry them through
        sale = Sale.objects.get(employee=self.emp)
        self.assertEqual(row.points_value, sale.points)

    def test_close_month_idempotent(self):
        targets_service.close_month(2026, 6)
        targets_service.close_month(2026, 6)
        self.assertEqual(
            MonthlyTargetHistory.objects.filter(employee=self.emp, product="SIP", year=2026, month=6).count(),
            1,
        )

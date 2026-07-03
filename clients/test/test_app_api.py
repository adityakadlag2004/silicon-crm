"""Tests for the native-app JSON API (clients/views/app_api.py).

Run: venv_new/bin/python manage.py test clients.test.test_app_api -v 2
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client, Employee, Sale


class AppDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="api_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="api_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.other_user = User.objects.create_user(username="api_other", password="x")
        cls.other = Employee.objects.create(user=cls.other_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="API Customer")

        today = timezone.localdate()
        Sale.objects.create(
            client=cls.customer, employee=cls.emp, product="SIP",
            amount=Decimal("5000"), status=Sale.STATUS_APPROVED, date=today,
        )
        Sale.objects.create(
            client=cls.customer, employee=cls.other, product="SIP",
            amount=Decimal("7000"), status=Sale.STATUS_PENDING, date=today,
        )

    def _get(self, user):
        http = TestClient()
        http.force_login(user)
        return http.get(reverse("clients:app_dashboard"))

    def test_requires_login(self):
        resp = TestClient().get(reverse("clients:app_dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_employee_sees_only_own_numbers(self):
        data = self._get(self.emp_user).json()
        self.assertEqual(data["role"], "employee")
        self.assertEqual(data["today"]["sales_count"], 1)
        self.assertEqual(data["today"]["amount"], 5000.0)
        # Recent list is scoped to own sales
        self.assertTrue(all(s["employee"] == "api_emp" for s in data["recent_sales"]))
        self.assertNotIn("pending_approvals", data)

    def test_admin_sees_firm_wide_and_approvals(self):
        data = self._get(self.admin_user).json()
        self.assertEqual(data["role"], "admin")
        self.assertEqual(data["today"]["amount"], 5000.0)  # approved only
        self.assertEqual(data["pending_approvals"], 1)
        self.assertEqual(len(data["recent_sales"]), 2)  # firm-wide list

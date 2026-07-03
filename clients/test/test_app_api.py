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


class AppScreenApiTests(TestCase):
    """Covers the screen APIs added for the native migration (v3.1)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="s_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="s_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Screen Customer", phone="9812345678", mapped_to=cls.emp)

        from clients.models import Product
        cls.product, _ = Product.objects.get_or_create(
            name="SIP", defaults={"code": "SIP"}
        )

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def test_sale_meta_employee_has_no_employee_list(self):
        data = self._http(self.emp_user).get(reverse("clients:app_sale_meta")).json()
        self.assertFalse(data["is_admin"])
        self.assertNotIn("employees", data)
        self.assertTrue(any(p["name"] == "SIP" for p in data["products"]))

    def test_sale_create_employee_is_pending_and_self_attributed(self):
        import json as _json
        resp = self._http(self.emp_user).post(
            reverse("clients:app_sale_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "amount": "5000", "employee_id": self.admin_emp.id,  # spoof attempt
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.status, Sale.STATUS_PENDING)
        self.assertEqual(sale.employee, self.emp)  # spoof ignored

    def test_sale_create_admin_auto_approves(self):
        import json as _json
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "amount": "9000", "employee_id": self.emp.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.json()["status"], "approved")
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.employee, self.emp)

    def test_clients_scopes(self):
        data = self._http(self.emp_user).get(reverse("clients:app_clients"), {"scope": "my"}).json()
        self.assertEqual(len(data["results"]), 1)
        data = self._http(self.emp_user).get(reverse("clients:app_clients"), {"q": "zzz-no-match"}).json()
        self.assertEqual(len(data["results"]), 0)

    def test_client_detail(self):
        data = self._http(self.emp_user).get(
            reverse("clients:app_client_detail", args=[self.customer.id])
        ).json()
        self.assertEqual(data["name"], "Screen Customer")

    def test_sales_list_scoped_and_approve_flow(self):
        import json as _json
        sale = Sale.objects.create(
            client=self.customer, employee=self.emp, product="SIP",
            amount=Decimal("1000"), status=Sale.STATUS_PENDING,
        )
        # Employee sees own, cannot approve
        data = self._http(self.emp_user).get(reverse("clients:app_sales")).json()
        self.assertFalse(data["can_approve"])
        self.assertTrue(all(r["employee"] for r in data["results"]))
        resp = self._http(self.emp_user).post(
            reverse("clients:app_sale_action", args=[sale.id]),
            data=_json.dumps({"action": "approve"}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        # Admin approves
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_action", args=[sale.id]),
            data=_json.dumps({"action": "approve"}), content_type="application/json",
        )
        self.assertEqual(resp.json()["status"], "approved")

    def test_followups_and_action(self):
        import json as _json
        from django.utils import timezone as tz
        from clients.models import CallFollowUp
        fu = CallFollowUp.objects.create(employee=self.emp, phone="123", scheduled_at=tz.now())
        data = self._http(self.emp_user).get(reverse("clients:app_followups")).json()
        self.assertEqual(len(data["pending"]), 1)
        resp = self._http(self.emp_user).post(
            reverse("clients:app_followup_action", args=[fu.id]),
            data=_json.dumps({"action": "done"}), content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_DONE)

    def test_logout(self):
        http = self._http(self.emp_user)
        self.assertTrue(http.post(reverse("clients:app_logout")).json()["ok"])
        # Session is dead now
        resp = http.get(reverse("clients:app_dashboard"))
        self.assertEqual(resp.status_code, 302)

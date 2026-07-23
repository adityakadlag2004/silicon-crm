"""Mobile-app PPT support: sale-meta ships PPT options (admin gets FYC), and
sale-create requires + snapshots the PPT margin — mirroring the web."""

import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, PlanPptRate, Product, Sale


class AppPptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("ppt_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user("ppt_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="PPT Customer")

        parent, _ = Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance"})
        parent.is_active = True
        parent.save()
        cls.plan = Product.objects.create(name="App Plan", code="APLAN", parent=parent, is_active=True)
        PlanPptRate.objects.create(product=cls.plan, designation="advisor", ppt="10", fyc=Decimal("12.00"))
        PlanPptRate.objects.create(product=cls.plan, designation="mdrt", ppt="10", fyc=Decimal("24.00"))

    def _http(self, user):
        http = TestClient()
        http.force_login(user)
        return http

    def _meta(self, user):
        return self._http(user).get(reverse("clients:app_sale_meta")).json()

    def test_meta_ships_ppt_options_to_everyone(self):
        for user in (self.admin_user, self.emp_user):
            data = self._meta(user)
            sub = self._find_subproduct(data, "App Plan")
            self.assertEqual(sub["ppt_options"], ["10"])

    def test_meta_fyc_is_admin_only(self):
        self.assertIn("ppt_fyc", self._meta(self.admin_user))
        self.assertNotIn("ppt_fyc", self._meta(self.emp_user))

    def test_create_requires_ppt(self):
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_create"),
            data=json.dumps({"client_id": self.customer.id, "product_id": self.plan.id,
                             "amount": "100000"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("PPT", resp.json()["error"])

    def test_create_snapshots_margin(self):
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_create"),
            data=json.dumps({"client_id": self.customer.id, "product_id": self.plan.id,
                             "ppt": "10", "amount": "100000"}),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        sale = Sale.objects.get(id=resp.json()["id"])
        self.assertEqual(sale.ppt, "10")
        self.assertEqual(sale.margin_percent_snapshot, Decimal("12.00"))  # Advisor default

    def _find_subproduct(self, data, name):
        for p in data["products"]:
            for s in p.get("subproducts", []):
                if s["name"] == name:
                    return s
        raise AssertionError(f"sub-product {name} not in meta")

"""Sub-products: a child Product carries its own margin, inherits its
category's insurance behaviour, and shows as 'Category › Sub-product' in the
business-analytics earnings breakdown."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clients.models import Client, Employee, Product, Sale
from clients.forms import (
    AdminSaleForm, _main_product_choices, _product_children_map, _subproduct_choices,
)
from clients.views.reports import _month_margin_breakdown


class SubProductMarginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sp_admin", password="pass")
        self.emp = Employee.objects.create(user=self.user, role="admin", active=True)
        self.client_obj = Client.objects.create(name="ACME", phone="9990001111")

        self.life, _ = Product.objects.get_or_create(
            code="LIFE_INS",
            defaults={"name": "Life Insurance", "domain": Product.DOMAIN_SALE},
        )
        Product.objects.filter(pk=self.life.pk).update(
            margin_percent=Decimal("5.00"), domain=Product.DOMAIN_SALE
        )
        self.life.refresh_from_db()
        self.term = Product.objects.create(
            name="Term Plan", code="TERM", parent=self.life,
            domain=Product.DOMAIN_SALE, margin_percent=Decimal("20.00"), display_order=2,
        )

    def _sale(self, product, amount, policy_type=""):
        return Sale.objects.create(
            client=self.client_obj, employee=self.emp, product=product.name,
            product_ref=product, amount=Decimal(amount), status="approved",
            policy_type=policy_type, date=date(2026, 7, 10),
            policy_date=date(2026, 7, 10), policy_number="X1",
        )

    def test_child_margin_used_not_parent(self):
        self._sale(self.term, "100000")
        rows, _ = _month_margin_breakdown(2026, 7)
        term_row = next(r for r in rows if r["product"] == "Life Insurance › Term Plan")
        # 20% (child), not 5% (parent).
        self.assertEqual(term_row["margin_percent"], Decimal("20.00"))
        self.assertEqual(term_row["margin_amount"], Decimal("20000.00"))

    def test_child_is_insurance(self):
        s = self._sale(self.term, "50000")
        self.assertTrue(self.term.is_insurance)
        self.assertTrue(s.is_insurance)          # drives policy-date requirement
        self.assertFalse(self.term.is_health)    # Life, not Health → no Fresh/Port

    def test_bulk_add_creates_subproducts_and_skips_dupes(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse("clients:product_management"), {
            "action": "bulk_add",
            "bulk_name": ["ULIP", "", "Life Insurance", "Endowment"],
            "bulk_parent_id": [str(self.life.pk), "", "", str(self.life.pk)],
            "bulk_margin": ["3", "0", "0", "8"],
        })
        self.assertEqual(resp.status_code, 302)
        ulip = Product.objects.get(name="ULIP")
        self.assertEqual(ulip.parent_id, self.life.pk)
        self.assertEqual(ulip.margin_percent, Decimal("3"))
        self.assertEqual(Product.objects.get(name="Endowment").parent_id, self.life.pk)
        # blank row skipped; "Life Insurance" already exists → not duplicated.
        self.assertFalse(Product.objects.filter(name="").exists())
        self.assertEqual(Product.objects.filter(name="Life Insurance").count(), 1)

    def test_two_step_choices(self):
        mains = [v for v, _ in _main_product_choices()]
        self.assertIn("Life Insurance", mains)          # category is a main option
        self.assertNotIn("Term Plan", mains)            # sub-product is NOT a main option
        self.assertIn("Term Plan", [v for v, _ in _subproduct_choices()])
        self.assertEqual(_product_children_map()["Life Insurance"], ["Term Plan"])

    def _base_form_data(self, **over):
        data = {
            "client": self.client_obj.pk, "amount": "50000",
            "date": "2026-07-10", "policy_date": "2026-07-10",
            "policy_number": "P1", "policy_type": "",
        }
        data.update(over)
        return data

    def test_subproduct_required_when_main_has_children(self):
        form = AdminSaleForm(self._base_form_data(product="Life Insurance"))
        self.assertFalse(form.is_valid())
        self.assertIn("subproduct", form.errors)

    def test_subproduct_becomes_effective_product(self):
        form = AdminSaleForm(self._base_form_data(
            product="Life Insurance", subproduct="Term Plan", employee=self.emp.pk))
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save(commit=False)
        self.assertEqual(sale.product, "Term Plan")     # sub-product is what's sold

    def test_main_without_children_needs_no_subproduct(self):
        Product.objects.create(
            name="Standalone Widget", code="STANDALONE_W",
            domain=Product.DOMAIN_SALE, margin_percent=Decimal("10"))
        form = AdminSaleForm(self._base_form_data(product="Standalone Widget", employee=self.emp.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).product, "Standalone Widget")

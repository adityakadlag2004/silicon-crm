"""Sub-products: a child Product carries its own margin and inherits its
category's insurance behaviour, but it is a sale-entry detail only — every
report and picker outside sale/renewal entry deals in main products, with the
child's business (at the child's own rate) folded into its category."""
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
        """Rolled up under the category, priced at the child's own rate."""
        self._sale(self.term, "100000")
        rows, _ = _month_margin_breakdown(2026, 7)

        # No sub-product row anywhere — the report deals in main products.
        self.assertEqual([r["product"] for r in rows if "›" in r["product"]], [])
        life_row = next(r for r in rows if r["product"] == "Life Insurance")
        # 20% (child), not 5% (parent).
        self.assertEqual(life_row["margin_percent"], Decimal("20.00"))
        self.assertEqual(life_row["margin_amount"], Decimal("20000.00"))

    def test_category_row_blends_its_plans_rates(self):
        """Parent sale at 5% + child sale at 20% = one row, blended honestly."""
        self._sale(self.life, "100000")
        self._sale(self.term, "100000")
        rows, _ = _month_margin_breakdown(2026, 7)

        life_rows = [r for r in rows if r["product"] == "Life Insurance"]
        self.assertEqual(len(life_rows), 1)
        self.assertEqual(life_rows[0]["revenue"], Decimal("200000"))
        self.assertEqual(life_rows[0]["margin_amount"], Decimal("25000.00"))   # 5000 + 20000
        self.assertEqual(life_rows[0]["margin_percent"], Decimal("12.50"))

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

    def test_long_plan_name_saves(self):
        """Life plan names off the FYC chart run past 50 chars — Sale.product
        must hold whatever Product.name (100) holds, or the form rejects the sale."""
        long_name = ("Fortune Guarantee Supreme - Immediate Income | Deferred Income "
                     "ROP or NROP | Power of 6 | Premium Offset")[:100]
        Product.objects.create(
            name=long_name, code="FG_SUPREME", parent=self.life,
            domain=Product.DOMAIN_SALE, margin_percent=Decimal("15"))
        form = AdminSaleForm(self._base_form_data(
            product="Life Insurance", subproduct=long_name, employee=self.emp.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().product, long_name)

    def test_main_without_children_needs_no_subproduct(self):
        Product.objects.create(
            name="Standalone Widget", code="STANDALONE_W",
            domain=Product.DOMAIN_SALE, margin_percent=Decimal("10"))
        form = AdminSaleForm(self._base_form_data(product="Standalone Widget", employee=self.emp.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).product, "Standalone Widget")


class MainProductsOnlyOutsideSaleEntryTests(TestCase):
    """Sub-products are selectable when entering a sale or a renewal, and
    nowhere else. Every other picker offers main products only, and the
    business behind a sub-product counts towards its category."""

    def setUp(self):
        self.user = User.objects.create_user("mainonly_admin", password="pw", is_superuser=True)
        self.emp = Employee.objects.create(user=self.user, role="admin", salary=0, active=True)
        self.client.force_login(self.user)

        self.life, _ = Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance"},
        )
        Product.objects.filter(pk=self.life.pk).update(
            domain=Product.DOMAIN_BOTH, is_active=True, margin_percent=Decimal("5.00"),
        )
        self.life.refresh_from_db()
        self.plan = Product.objects.create(
            name="Guaranteed Return Plan", code="GRP", parent=self.life,
            domain=Product.DOMAIN_BOTH, is_active=True, margin_percent=Decimal("20.00"),
        )

    def test_lead_requirements_offer_main_products_only(self):
        from clients.forms import LeadInterestFormSet

        products = list(LeadInterestFormSet().forms[0].fields["product"].queryset)
        self.assertIn(self.life, products)
        self.assertNotIn(self.plan, products)

    def test_app_lead_meta_offers_main_products_only(self):
        names = [
            p["name"] for p in
            self.client.get(reverse("clients:app_lead_meta")).json()["products"]
        ]
        self.assertIn("Life Insurance", names)
        self.assertNotIn("Guaranteed Return Plan", names)

    def test_app_refuses_a_subproduct_as_a_lead_requirement(self):
        import json as _json
        from clients.models import Lead

        lead = Lead.objects.create(customer_name="Sub Seeker", assigned_to=self.emp)
        resp = self.client.post(
            reverse("clients:app_lead_interest", args=[lead.id]),
            data=_json.dumps({"product_id": self.plan.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(lead.interests.count(), 0)

    def test_client_filters_and_campaign_and_rule_pickers_are_main_only(self):
        for url_name, needle in (
            ("clients:all_clients", f'name="product_{self.plan.id}_status"'),
            ("clients:manage_campaigns", None),
            ("clients:manage_incentive_rules", None),
        ):
            html = self.client.get(reverse(url_name)).content.decode()
            self.assertIn("Life Insurance", html, url_name)
            if needle:
                self.assertNotIn(needle, html, url_name)
                self.assertIn(f'name="product_{self.life.id}_status"', html, url_name)
            else:
                self.assertNotIn("Guaranteed Return Plan", html, url_name)

    def test_sale_and_renewal_entry_still_offer_subproducts(self):
        from clients.forms import RenewalForm, _subproduct_choices

        self.assertIn(("Guaranteed Return Plan", "Guaranteed Return Plan"), _subproduct_choices())
        self.assertIn(self.plan, list(RenewalForm().fields["product_ref"].queryset))

        meta = self.client.get(reverse("clients:app_sale_meta")).json()
        blob = str(meta)
        self.assertIn("Guaranteed Return Plan", blob)          # app Add Sale keeps plans

    def test_a_clients_plan_business_answers_the_category_filter(self):
        """A client whose only life business is a plan still matches the
        Life Insurance filter — the roll-up, not just the picker."""
        holder = Client.objects.create(name="Plan Holder", phone="9000000009", mapped_to=self.emp)
        Client.objects.create(name="Nobody", phone="9000000008", mapped_to=self.emp)
        Sale.objects.create(
            client=holder, employee=self.emp, product=self.plan.name, product_ref=self.plan,
            amount=Decimal("100000"), status="approved", date=date(2026, 7, 10),
            policy_date=date(2026, 7, 10), policy_number="GRP1",
        )

        html = self.client.get(
            reverse("clients:all_clients"), {f"product_{self.life.id}_status": "yes"},
        ).content.decode()
        self.assertIn("Plan Holder", html)
        self.assertNotIn("Nobody", html)

    def test_a_category_campaign_covers_its_plans(self):
        """A campaign is set on the main product, so a plan sale must earn it."""
        from datetime import timedelta

        from clients.models import Campaign, CampaignProduct

        today = date(2026, 7, 10)
        campaign = Campaign.objects.create(
            name="Life Push", start_date=today - timedelta(days=5),
            end_date=today + timedelta(days=5), is_active=True,
        )
        CampaignProduct.objects.create(
            campaign=campaign, product_ref=self.life,
            benefit_type=CampaignProduct.BENEFIT_UNIT, unit_amount=Decimal("500"),
        )
        client_row = Client.objects.create(name="Camp Client", phone="9000000007")
        sale = Sale.objects.create(
            client=client_row, employee=self.emp, product=self.plan.name, product_ref=self.plan,
            amount=Decimal("100000"), status="approved", date=today,
            policy_date=today, policy_number="GRP2",
        )
        self.assertIsNotNone(sale._active_campaign_product())
        self.assertEqual(sale._active_campaign_product().campaign, campaign)

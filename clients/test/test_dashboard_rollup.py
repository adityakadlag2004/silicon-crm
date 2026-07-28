"""The dashboards show one row per product CATEGORY: sub-product sales fold into
their parent, and sub-product rows never appear."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client, Employee, Product, Sale


class DashboardRollupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("dash_admin", password="x")
        cls.admin = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.customer = Client.objects.create(name="Dash Customer")

        cls.cat, _ = Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance"})
        cls.cat.is_active = True
        cls.cat.save()
        cls.sub_a = Product.objects.create(name="Roll Plan A", code="RPA", parent=cls.cat, is_active=True)
        cls.sub_b = Product.objects.create(name="Roll Plan B", code="RPB", parent=cls.cat, is_active=True)

        today = timezone.localdate()
        for sub, amt in ((cls.sub_a, "100000"), (cls.sub_b, "60000")):
            Sale.objects.create(
                client=cls.customer, employee=cls.admin, product=sub.name, product_ref=sub,
                amount=Decimal(amt), status=Sale.STATUS_APPROVED, date=today,
            )

    def _ctx(self, url_name):
        http = TestClient()
        http.force_login(self.admin_user)
        resp = http.get(reverse(f"clients:{url_name}"))
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_admin_dashboard_rolls_subproducts_into_category(self):
        resp = self._ctx("admin_dashboard")
        rows = {r["product"]: r for r in resp.context["overall_monthly_progress"]}
        self.assertIn("Life Insurance", rows)
        self.assertEqual(rows["Life Insurance"]["achieved"], Decimal("160000"))
        # Sub-products must not appear as their own rows.
        self.assertNotIn("Roll Plan A", rows)
        self.assertNotIn("Roll Plan B", rows)
        # And not anywhere in the rendered page.
        self.assertNotContains(resp, "Roll Plan A")

    def test_employee_dashboard_rolls_subproducts_into_category(self):
        resp = self._ctx("employee_dashboard")
        rows = {r["product"]: r for r in resp.context["product_sales_breakup"]}
        self.assertEqual(rows["Life Insurance"]["amount"], Decimal("160000"))
        self.assertNotIn("Roll Plan A", rows)
        self.assertNotContains(resp, "Roll Plan B")

    def test_admin_overview_headline_numbers(self):
        resp = self._ctx("admin_dashboard")
        o = resp.context["overview"]
        # Premium + product mix read at category level, sub-products folded in.
        self.assertEqual(o["mtd_premium"], Decimal("160000"))
        mix = {m["name"]: m["amount"] for m in o["product_mix"]}
        self.assertEqual(mix.get("Life Insurance"), 160000.0)
        self.assertNotIn("Roll Plan A", mix)
        # Nothing pending / no claims in this fixture.
        self.assertEqual(o["pending_count"], 0)
        self.assertEqual(o["open_claims"], 0)
        # The seller shows on the leaderboard with the full category premium.
        names = {r["name"]: r["premium"] for r in o["leaderboard"]}
        self.assertEqual(names.get("dash_admin"), Decimal("160000"))
        # New overview sections are actually rendered.
        self.assertContains(resp, "Needs attention")
        self.assertContains(resp, "Team leaderboard")

    # ── Reports roll up the same way as the dashboards ────────────────────────

    def test_business_overview_has_no_subproduct_columns(self):
        resp = self._ctx("business_overview")
        buckets = resp.context["data"]["buckets"]
        self.assertIn("Life Insurance", buckets)
        self.assertNotIn("Roll Plan A", buckets)
        self.assertNotIn("Roll Plan B", buckets)
        # Both sub-product sales land in the parent's column of the newest period.
        idx = buckets.index("Life Insurance")
        self.assertEqual(resp.context["data"]["trend"][-1]["by_product"][idx], Decimal("160000"))
        # ...and not swept into a catch-all. ("Other" is itself a seeded
        # product, so the invariant is one column per name, never a duplicate.)
        self.assertEqual(len(buckets), len(set(buckets)))
        mix = {p["name"]: p["amount"] for p in resp.context["data"]["products"]}
        self.assertEqual(mix.get("Life Insurance"), Decimal("160000"))
        self.assertNotIn("Roll Plan A", mix)
        # Leaderboard splits by the same category columns.
        self.assertEqual(resp.context["data"]["leaderboard"][0]["by_product"][idx], Decimal("160000"))
        self.assertNotContains(resp, "Roll Plan A")

    def _month_page(self, url_name):
        today = timezone.localdate()
        http = TestClient()
        http.force_login(self.admin_user)
        resp = http.get(reverse(f"clients:{url_name}", args=[today.year, today.month]))
        self.assertEqual(resp.status_code, 200)
        return resp

    def test_past_month_performance_rolls_subproducts(self):
        resp = self._month_page("past_month_performance")
        rows = {r["product"]: r for r in resp.context["products"]}
        self.assertEqual(rows["Life Insurance"]["total_amount"], Decimal("160000"))
        self.assertNotIn("Roll Plan A", rows)
        self.assertNotContains(resp, "Roll Plan B")

    def test_admin_past_month_performance_rolls_subproducts(self):
        resp = self._month_page("admin_past_month_performance")
        rows = {r["product"]: r for r in resp.context["products"]}
        self.assertEqual(rows["Life Insurance"]["total_amount"], 160000.0)
        self.assertNotIn("Roll Plan A", rows)
        # The seller appears once per category, not once per sub-product.
        stats = {s["product"]: s for s in resp.context["product_employee_stats"]}
        sellers = stats["Life Insurance"]["employees"]
        self.assertEqual(len(sellers), 1)
        self.assertEqual(sellers[0]["total_amount"], 160000.0)
        self.assertNotContains(resp, "Roll Plan A")

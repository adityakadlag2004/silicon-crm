"""Life premium payment mode: the seller types the yearly premium, the sale is
booked at the first instalment collected, and only that earns points."""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.forms import AdminSaleForm, EditSaleForm
from clients.models import (Client, Employee, IncentiveRule, InsurancePolicy, Product,
                            Renewal, Sale)
from clients.services import incentives as inc
from clients.services import insurance_sync


class LifePremiumModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.life, _ = Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance", "domain": "both"})
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance", "domain": "both"})
        cls.plan = Product.objects.create(name="PR Life Pro", code="PRLP", parent=cls.life, domain="both")
        cls.rule = IncentiveRule.objects.create(
            product=cls.life.name, product_ref=cls.life,
            unit_amount=Decimal("100"), points_per_unit=Decimal("1.75"),
            slab_period=IncentiveRule.PERIOD_FY)
        u = User.objects.create_superuser("lpm_admin", "a@x.com", "pw")
        cls.emp = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(name="Meera Joshi", phone="9876500011")

    def _sale(self, mode, yearly=Decimal("60000"), product=None, **kw):
        d = dict(client=self.client_rec, employee=self.emp, product_ref=product or self.plan,
                 amount=yearly, yearly_premium=yearly, premium_mode=mode,
                 status=Sale.STATUS_APPROVED, date=date(2026, 5, 10),
                 policy_date=date(2026, 1, 31), policy_number="LIF1")
        d.update(kw)
        return Sale.objects.create(**d)

    def test_amount_is_the_first_instalment(self):
        for mode, collected in (("yearly", 60000), ("half_yearly", 30000),
                                ("quarterly", 15000), ("monthly", 5000)):
            s = self._sale(mode)
            self.assertEqual(s.amount, Decimal(collected), mode)
            self.assertEqual(s.yearly_premium, Decimal("60000"))

    def test_points_and_fy_volume_read_the_first_instalment_only(self):
        s = self._sale("half_yearly")
        self.assertEqual(s.points, Decimal("525.000"))           # 1.75% of 30,000
        volume, _, _ = inc.period_totals(self.rule, self.emp, s.date, is_health=False)
        self.assertEqual(volume, Decimal("30000"))

    def test_other_products_are_always_yearly(self):
        s = self._sale("half_yearly", product=self.health, policy_type="fresh")
        self.assertEqual((s.premium_mode, s.yearly_premium, s.amount),
                         ("yearly", None, Decimal("60000")))

    def test_cover_runs_to_the_next_instalment(self):
        self.assertEqual(self._sale("half_yearly").coverage_end(), date(2026, 7, 31))
        self.assertEqual(self._sale("monthly").coverage_end(), date(2026, 2, 28))
        self.assertEqual(self._sale("yearly").coverage_end(), date(2027, 1, 31))

    def test_tracker_policy_chases_each_instalment(self):
        s = self._sale("half_yearly")
        policy = insurance_sync.sync_policy_from_sale(s)
        self.assertEqual((policy.premium_amount, policy.end_date),
                         (Decimal("30000"), date(2026, 7, 31)))
        # The second half is a renewal: it pays six more months, not a year.
        r = Renewal.objects.create(client=self.client_rec, employee=self.emp,
                                   product_ref=self.life, product_type=Renewal.PRODUCT_TYPE_LIFE,
                                   frequency="half_yearly", renewal_date=date(2026, 7, 31),
                                   premium_amount=30000)
        insurance_sync.link_renewal_to_policy(r, selected_policy_id=policy.id)
        policy.refresh_from_db()
        self.assertEqual(policy.end_date, date(2027, 1, 31))

    def test_web_form_books_the_instalment_and_edit_reopens_on_yearly(self):
        form = AdminSaleForm(data={
            "client": self.client_rec.id, "employee": self.emp.id,
            "product": "Life Insurance", "subproduct": "PR Life Pro",
            "amount": "1,20,000", "premium_mode": "quarterly", "date": "2026-05-10",
            "policy_date": "2026-05-01", "policy_number": "lif9", "insurer": "Tata AIA",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("30000.00"))
        sale = form.save(commit=False)
        sale.product_ref = self.plan
        sale.save()
        self.assertEqual((sale.amount, sale.yearly_premium), (Decimal("30000"), Decimal("120000")))
        self.assertEqual(EditSaleForm(instance=sale).initial["amount"], Decimal("120000"))

    def test_app_sends_is_life_and_books_the_instalment(self):
        http = TestClient()
        http.force_login(self.emp.user)
        meta = http.get(reverse("clients:app_sale_meta")).json()
        self.assertTrue(next(p for p in meta["products"] if p["name"] == "Life Insurance")["is_life"])
        body = {"client_id": self.client_rec.id, "product_id": self.plan.id, "amount": "48000",
                "policy_date": "2026-05-01", "policy_number": "APP1", "premium_mode": "monthly"}
        r = http.post(reverse("clients:app_sale_create"), json.dumps(body),
                      content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        sale = Sale.objects.get(pk=r.json()["id"])
        self.assertEqual((sale.amount, sale.yearly_premium, sale.premium_mode),
                         (Decimal("4000"), Decimal("48000"), "monthly"))
        body.update(premium_mode="weekly", policy_number="APP2")
        r = http.post(reverse("clients:app_sale_create"), json.dumps(body),
                      content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(InsurancePolicy.objects.filter(policy_number="APP2").exists())

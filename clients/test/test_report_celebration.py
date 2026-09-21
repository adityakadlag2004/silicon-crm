"""Monthly Business Report's celebration section: top performer per product, admin-only."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, Product, Sale


class ReportCelebrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("cel_admin", password="x", first_name="Asha")
        cls.mgr = User.objects.create_user("cel_mgr", password="x", first_name="Ravi")
        cls.other = User.objects.create_user("cel_emp", password="x", first_name="Neha")
        a = Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        m = Employee.objects.create(user=cls.mgr, role="manager", salary=0, active=True)
        e = Employee.objects.create(user=cls.other, role="employee", salary=0, active=True)
        sip = Product.objects.create(name="Cel SIP", code="CELSIP", display_order=1)
        Product.objects.create(name="Cel Empty", code="CELEMPTY", display_order=2)
        c = Client.objects.create(name="Cel C", mapped_to=a)
        d = date(2026, 4, 9)
        for emp, amt, pts in ((a, "3000", 50), (m, "3000", 40), (e, "1000", 10)):
            s = Sale.objects.create(client=c, employee=emp, product="Cel SIP", product_ref=sip,
                                    amount=Decimal(amt), status="approved", date=d)
            Sale.objects.filter(pk=s.pk).update(points=pts)  # pin points; save() prices them
        # March: Asha topped SIP too, and Neha out-pointed everyone.
        for emp, amt, pts in ((a, "2000", 45), (e, "500", 60)):
            s = Sale.objects.create(client=c, employee=emp, product="Cel SIP", product_ref=sip,
                                    amount=Decimal(amt), status="approved", date=date(2026, 3, 5))
            Sale.objects.filter(pk=s.pk).update(points=pts)

    def _get(self, user, url="clients:monthly_business_report", params=None):
        http = TestClient()
        http.force_login(user)
        return http.get(reverse(url), params or {"month": 4, "year": 2026})

    def test_admin_sees_top_performers_with_ties(self):
        cel = self._get(self.admin).context["celebration"]
        self.assertEqual(len(cel["products"]), 1)  # a product with no business has no winner
        top = cel["products"][0]
        self.assertEqual((top["product"], top["amount"]), ("Cel SIP", Decimal("3000")))
        self.assertEqual(sorted(top["names"]), ["Asha", "Ravi"])
        self.assertEqual(top["repeat"], ["Asha"])  # topped SIP in March as well

    def test_star_improved_and_team_change(self):
        cel = self._get(self.admin).context["celebration"]
        self.assertEqual(cel["star"]["names"], ["Asha"])
        # Ravi 0 -> 40 beats Asha 45 -> 50; Neha went down.
        self.assertEqual(cel["improved"], {"amount": 40, "names": ["Ravi"]})
        self.assertEqual(cel["points_change"], -5)  # 100 vs 105
        self.assertContains(self._get(self.admin), "Star of the Month")

    def test_january_compares_with_previous_december(self):
        resp = self._get(self.admin, params={"month": 1, "year": 2026})
        self.assertEqual(resp.context["prev_month_name"], "December")

    def test_manager_does_not_see_it(self):
        resp = self._get(self.mgr)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("celebration", resp.context)
        self.assertNotContains(resp, "Star of the Month")

    def test_not_on_daily_report(self):
        resp = self._get(self.admin, "clients:daily_business_report", {"date": "2026-04-09"})
        self.assertNotIn("celebration", resp.context)

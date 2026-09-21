"""Monthly Business Report celebration: winners, streaks, target champions,
certificates and the month-end announcement — all admin-only on the web."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import (Client, Employee, EmployeeTarget, MonthlyTargetHistory,
                            Notification, Product, Sale)
from clients.services import business_report


class ReportCelebrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("cel_admin", password="x", first_name="Asha")
        cls.mgr = User.objects.create_user("cel_mgr", password="x", first_name="Ravi")
        cls.other = User.objects.create_user("cel_emp", password="x", first_name="Neha")
        cls.a = a = Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        m = Employee.objects.create(user=cls.mgr, role="manager", salary=0, active=True)
        e = Employee.objects.create(user=cls.other, role="employee", salary=0, active=True)
        cls.sip = sip = Product.objects.create(name="Cel SIP", code="CELSIP", display_order=1)
        Product.objects.create(name="Cel Empty", code="CELEMPTY", display_order=2)
        cls.c = c = Client.objects.create(name="Cel C", mapped_to=a)
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
        # February: Asha on top again -> a three-month run by April.
        Sale.objects.create(client=c, employee=a, product="Cel SIP", product_ref=sip,
                            amount=Decimal("100"), status="approved", date=date(2026, 2, 3))
        # April's targets as close_month recorded them: Neha hit her only one,
        # Asha hit SIP but not her other target.
        for emp, prod, t in ((a, "Cel SIP", "2500"), (a, "Cel Empty", "100"), (e, "Cel SIP", "1000")):
            MonthlyTargetHistory.objects.create(employee=emp, product=prod, year=2026, month=4,
                                                target_value=Decimal(t))
        cls.m, cls.e = m, e

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
        self.assertEqual(top["streaks"], {"Asha": 3, "Ravi": 1})  # Feb, Mar, Apr

    def test_star_improved_and_team_change(self):
        cel = self._get(self.admin).context["celebration"]
        self.assertEqual(cel["star"]["names"], ["Asha"])
        # Ravi 0 -> 40 beats Asha 45 -> 50; Neha went down.
        self.assertEqual(cel["improved"], {"amount": 40, "names": ["Ravi"]})
        self.assertEqual(cel["points_change"], -5)  # 100 vs 105
        self.assertContains(self._get(self.admin), "Star of the Month")

    def test_january_compares_with_previous_december(self):
        resp = self._get(self.admin, params={"month": 1, "year": 2026})
        self.assertEqual(resp.context["celebration"]["prev_month_name"], "December")

    def test_target_champions_from_closed_month_history(self):
        t = self._get(self.admin).context["celebration"]["targets"]
        self.assertEqual([(c["name"], c["all"]) for c in t], [("Neha", True), ("Asha", False)])
        self.assertEqual(t[1]["hits"], [{"product": "Cel SIP", "pct": 120}])

    def test_open_month_reads_live_targets(self):
        today = date.today()
        Sale.objects.create(client=self.c, employee=self.m, product="Cel SIP", product_ref=self.sip,
                            amount=Decimal("600"), status="approved", date=today)
        EmployeeTarget.objects.create(employee=self.m, product="Cel SIP", target_value=Decimal("500"))
        t = business_report.celebration(today.year, today.month)["targets"]
        self.assertEqual([c["name"] for c in t], ["Ravi"])
        # A past month with no recorded history credits nobody against today's targets.
        self.assertEqual(business_report.month_targets(2026, 3), {})

    def test_certificates_page(self):
        resp = self._get(self.admin, "clients:celebration_certificates")
        self.assertContains(resp, "Certificate of Achievement")
        self.assertContains(resp, "admin/img/logo")
        self.assertContains(resp, "Star of the Month")
        self.assertContains(resp, "3 months running")
        self.assertContains(resp, "Target Champion")
        self.assertEqual(self._get(self.mgr, "clients:celebration_certificates").status_code, 403)

    def test_announce_reaches_everyone_once(self):
        self.assertEqual(business_report.announce(2026, 4), 3)
        n = Notification.objects.get(recipient=self.other, title="🎉 April 2026 celebration")
        self.assertIn("Star of the Month: Asha", n.body)
        self.assertEqual(business_report.announce(2026, 4), 0)  # re-run sends nothing
        self.assertEqual(business_report.announce(2025, 6), 0)  # no winners, no push

    def test_manager_does_not_see_it(self):
        resp = self._get(self.mgr)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("celebration", resp.context)
        self.assertNotContains(resp, "Star of the Month")

    def test_not_on_daily_report(self):
        resp = self._get(self.admin, "clients:daily_business_report", {"date": "2026-04-09"})
        self.assertNotIn("celebration", resp.context)

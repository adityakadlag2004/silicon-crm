"""Life-insurance PPT rates, MDRT financial-year toggle, and the active-plan picker."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from clients.models import FirmSettings, PlanPptRate, Product


class MdrtCalendarYearTests(TestCase):
    def test_toggle_auto_reverts_next_calendar_year(self):
        fs = FirmSettings.get_settings()
        fs.mdrt_active_year = 2026
        # MDRT runs Jan–Dec: active all through 2026...
        self.assertTrue(fs.is_mdrt_active(datetime.date(2026, 1, 1)))
        self.assertTrue(fs.is_mdrt_active(datetime.date(2026, 12, 31)))
        # ...and off the moment the next calendar year starts, no cron.
        self.assertFalse(fs.is_mdrt_active(datetime.date(2027, 1, 1)))

    def test_off_when_unset(self):
        self.assertFalse(FirmSettings.get_settings().is_mdrt_active())


class PptResolverTests(TestCase):
    def setUp(self):
        parent, _ = Product.objects.get_or_create(
            code="LIFE_INS", defaults={"name": "Life Insurance"})
        self.plan = Product.objects.create(name="Test Plan", code="TP", parent=parent)
        PlanPptRate.objects.create(product=self.plan, designation="advisor", ppt="5", fyc=Decimal("6.00"))
        PlanPptRate.objects.create(product=self.plan, designation="mdrt", ppt="5", fyc=Decimal("12.90"))
        PlanPptRate.objects.create(product=self.plan, designation="advisor", ppt="SP", fyc=Decimal("1.70"))

    def test_designation_selects_the_right_column(self):
        self.assertEqual(self.plan.fyc_for_ppt("5", mdrt=False), Decimal("6.00"))
        self.assertEqual(self.plan.fyc_for_ppt("5", mdrt=True), Decimal("12.90"))

    def test_ppt_choices_sorted_sp_first(self):
        self.assertEqual([p for p, _ in self.plan.ppt_choices()], ["SP", "5"])

    def test_missing_ppt_returns_none(self):
        self.assertIsNone(self.plan.fyc_for_ppt("99"))


class SnapshotTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from clients.models import Client, Employee

        parent, _ = Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance"})
        self.plan = Product.objects.create(name="Snap Plan", code="SNAP", parent=parent, is_active=True)
        PlanPptRate.objects.create(product=self.plan, designation="advisor", ppt="10", fyc=Decimal("12.00"))
        PlanPptRate.objects.create(product=self.plan, designation="mdrt", ppt="10", fyc=Decimal("24.00"))
        self.user = User.objects.create_user("agent")
        self.emp = Employee.objects.create(user=self.user, role="employee", salary=0, active=True)
        self.client_obj = Client.objects.create(name="Cust")

    def _make_sale(self, ppt="10"):
        from clients.models import Sale
        return Sale(client=self.client_obj, employee=self.emp, product=self.plan.name,
                    amount=Decimal("100000"), date=datetime.date(2026, 6, 1), ppt=ppt)

    def test_snapshot_uses_advisor_by_default(self):
        from clients.services import sales as svc
        sale = svc.finalize_new_sale(self._make_sale(), self.user, auto_approve=True)
        self.assertEqual(sale.margin_percent_snapshot, Decimal("12.00"))

    def test_snapshot_uses_mdrt_when_active(self):
        from clients.services import sales as svc
        fs = FirmSettings.get_settings()
        fs.mdrt_active_year = FirmSettings.current_mdrt_year()
        fs.save()
        sale = svc.finalize_new_sale(self._make_sale(), self.user, auto_approve=True)
        self.assertEqual(sale.margin_percent_snapshot, Decimal("24.00"))

    def test_no_ppt_no_snapshot(self):
        from clients.services import sales as svc
        sale = svc.finalize_new_sale(self._make_sale(ppt=""), self.user, auto_approve=True)
        self.assertIsNone(sale.margin_percent_snapshot)


class PageRenderTests(TestCase):
    """The new template paths render without error when real data is present."""

    def setUp(self):
        from django.contrib.auth.models import User
        from clients.models import Employee

        parent, _ = Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance"})
        self.plan = Product.objects.create(name="Render Plan", code="RP", parent=parent, is_active=True)
        PlanPptRate.objects.create(product=self.plan, designation="advisor", ppt="10", fyc=Decimal("12.00"))
        admin_user = User.objects.create_user("boss", password="x", is_superuser=True, is_staff=True)
        Employee.objects.create(user=admin_user, role="admin", salary=0, active=True)
        self.client.force_login(admin_user)

    def test_product_management_shows_picker_and_mdrt(self):
        resp = self.client.get(reverse("clients:product_subproducts", args=[self.plan.parent_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "MDRT Status")
        self.assertContains(resp, "Life Insurance Plans")
        self.assertContains(resp, "Render Plan")

    def test_add_sale_ships_ppt_data(self):
        resp = self.client.get(reverse("clients:add_sale"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="ppt-options"')
        self.assertContains(resp, "Render Plan")  # active plan reaches the picker data

    def test_admin_add_sale_shows_margin_data(self):
        resp = self.client.get(reverse("clients:add_sale"))
        self.assertContains(resp, 'id="ppt-fyc"')  # admin gets the FYC figures
        self.assertContains(resp, "12.00")

    def test_add_sale_hides_margin_from_non_admin(self):
        from django.contrib.auth.models import User
        from clients.models import Employee
        emp_user = User.objects.create_user("worker", password="x")
        Employee.objects.create(user=emp_user, role="employee", salary=0, active=True)
        self.client.force_login(emp_user)
        resp = self.client.get(reverse("clients:add_sale"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="ppt-options"')   # PPT dropdown still works
        self.assertNotContains(resp, 'id="ppt-fyc"')    # but no margin figures
        self.assertNotContains(resp, "12.00")

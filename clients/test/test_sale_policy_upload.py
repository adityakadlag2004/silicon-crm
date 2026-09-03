"""The sale's Drive tick: asked at entry, chased when blank, closeable later.

Same tick as Renewal.policy_doc_submitted, one product line up: an insurance
sale whose policy document isn't filed must be visible, not silently fine.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clients.models import Client, Employee, Notification, Product, Sale


class SalePolicyUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.health.domain = Product.DOMAIN_BOTH
        cls.health.save()
        cls.sip, _ = Product.objects.get_or_create(
            code="SIP", defaults={"name": "SIP"})
        cls.sip.domain = Product.DOMAIN_BOTH
        cls.sip.save()

        cls.admin_user = User.objects.create_superuser("upl_admin", password="x")
        cls.admin = Employee.objects.create(
            user=cls.admin_user, role=Employee.Role.ADMIN, salary=0, active=True)
        cls.user = User.objects.create_user("upl_emp", password="x")
        cls.emp = Employee.objects.create(
            user=cls.user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="UPL Client", mapped_to=cls.emp)

    def _post(self, **extra):
        data = {
            "client": self.customer.id, "employee": self.emp.id,
            "product": self.health.name, "amount": "20000",
            "cover_amount": "500000", "policy_type": "fresh",
            "date": "2026-05-10", "policy_date": "2026-05-10",
            "policy_number": "UPL900", "policy_years": 1, "emi_months": 0,
        }
        data.update(extra)
        self.client.force_login(self.user)
        return self.client.post(reverse("clients:add_sale"), data)

    def test_unticked_means_not_uploaded(self):
        self._post()
        self.assertFalse(Sale.objects.latest("id").policy_doc_submitted)

    def test_ticked_is_recorded_and_raises_no_alert(self):
        self._post(policy_doc_submitted="on")
        self.assertTrue(Sale.objects.latest("id").policy_doc_submitted)
        self.assertFalse(Notification.objects.filter(
            recipient=self.admin_user, title__contains="Policy not uploaded").exists())

    def test_admin_is_notified_when_the_policy_is_missing(self):
        self._post()
        self.assertTrue(Notification.objects.filter(
            recipient=self.admin_user, title__contains="Policy not uploaded").exists())

    def test_a_non_insurance_sale_never_raises_the_flag(self):
        self._post(product=self.sip.name, policy_type="", policy_date="",
                   policy_number="", cover_amount="")
        sale = Sale.objects.latest("id")
        self.assertFalse(sale.policy_doc_submitted)
        self.assertFalse(Notification.objects.filter(
            title__contains="Policy not uploaded").exists())

    def test_the_tick_renders_on_the_add_form(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("clients:add_sale")).content.decode()
        self.assertIn("Policy uploaded to Google Drive?", html)
        self.assertIn('id="client-drive"', html)

    def test_the_list_flags_an_unfiled_policy(self):
        self._post()
        self.client.force_login(self.admin_user)
        html = self.client.get(
            reverse("clients:all_sales") + "?client=%s" % self.customer.id).content.decode()
        self.assertIn("Policy not uploaded", html)

    def test_marking_it_uploaded_clears_the_flag_without_repricing(self):
        self._post()
        sale = Sale.objects.latest("id")
        points = sale.points
        self.client.force_login(self.user)
        self.client.post(reverse("clients:mark_policy_uploaded", args=[sale.id]))
        sale.refresh_from_db()
        self.assertTrue(sale.policy_doc_submitted)
        self.assertEqual(sale.points, points)
        self.assertEqual(sale.status, Sale.STATUS_PENDING)   # filing never un-approves

    def test_someone_elses_sale_cannot_be_marked(self):
        self._post()
        sale = Sale.objects.latest("id")
        other = User.objects.create_user("upl_other", password="x")
        Employee.objects.create(user=other, role="employee", salary=0, active=True)
        self.client.force_login(other)
        resp = self.client.post(reverse("clients:mark_policy_uploaded", args=[sale.id]))
        self.assertEqual(resp.status_code, 403)
        sale.refresh_from_db()
        self.assertFalse(sale.policy_doc_submitted)

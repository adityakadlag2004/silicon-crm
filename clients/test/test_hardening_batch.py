"""Tests for the security/robustness hardening batch: public-form throttle,
option-pollution block, safe date parsing, password validation, status sync.

Run: venv_new/bin/python manage.py test clients.test.test_hardening_batch -v 2
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, LeadSheet, LeadSheetColumn


class PublicFormHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="pf_owner", password="x")
        cls.emp = Employee.objects.create(user=user, role="admin", salary=0, active=True)
        cls.sheet = LeadSheet.objects.create(name="Public", owner=cls.emp, public_form_enabled=True)
        LeadSheetColumn.objects.create(
            sheet=cls.sheet, name="Name", field_key="name",
            type=LeadSheetColumn.TYPE_TEXT, display_order=0,
        )
        cls.status_col = LeadSheetColumn.objects.create(
            sheet=cls.sheet, name="Status", field_key="status",
            type=LeadSheetColumn.TYPE_STATUS, options=["new", "contacted"],
            display_order=1, show_on_public_form=True,
        )
        cls.url = reverse("clients:lead_sheet_public_form", args=[cls.sheet.public_token])

    def setUp(self):
        cache.clear()  # throttle counters are cache-backed
        self.anon = TestClient()

    def test_public_submission_cannot_add_dropdown_options(self):
        resp = self.anon.post(self.url, {"col_name": "Visitor", "col_status": "hacked-option"})
        self.assertEqual(resp.status_code, 200)
        self.status_col.refresh_from_db()
        self.assertEqual(self.status_col.options, ["new", "contacted"])
        record = self.sheet.records.get()
        self.assertEqual(record.values.get("status"), "")  # unknown value rejected

    def test_public_form_is_rate_limited(self):
        for i in range(5):
            resp = self.anon.post(self.url, {"col_name": f"Visitor {i}"})
            self.assertNotEqual(resp.status_code, 429, f"request {i+1} throttled too early")
        resp = self.anon.post(self.url, {"col_name": "Visitor 6"})
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(self.sheet.records.count(), 5)

    def test_get_requests_not_throttled(self):
        for _ in range(10):
            self.assertEqual(self.anon.get(self.url).status_code, 200)


class SafeDateFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="df_admin", password="x")
        Employee.objects.create(user=user, role="admin", salary=0, active=True)
        cls.user = user

    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.user)

    def test_all_sales_survives_garbage_dates(self):
        resp = self.http.get(reverse("clients:all_sales"), {"start_date": "banana", "end_date": "07/2026"})
        self.assertEqual(resp.status_code, 200)

    def test_approve_sales_survives_garbage_dates(self):
        resp = self.http.get(reverse("clients:approve_sales"), {"start_date": "x", "end_date": "y"})
        self.assertEqual(resp.status_code, 200)

    def test_client_analysis_survives_garbage_dates(self):
        resp = self.http.get(reverse("clients:client_analysis"), {"start_date": "!!", "end_date": "0"})
        self.assertEqual(resp.status_code, 200)

    def test_monthly_report_survives_garbage_month(self):
        resp = self.http.get(reverse("clients:monthly_business_report"), {"month": "banana", "year": "x"})
        self.assertEqual(resp.status_code, 200)

    def test_business_overview_renders_with_data(self):
        from decimal import Decimal
        from django.utils import timezone as tz
        from clients.models import Product, Sale
        Product.objects.get_or_create(name="SIP", defaults={"code": "SIP", "display_order": 1})
        emp = Employee.objects.get(user=self.user)
        c = Client.objects.create(name="BO C")
        Sale.objects.create(client=c, employee=emp, product="SIP", amount=Decimal("1000"),
                            status="approved", date=tz.localdate())
        resp = self.http.get(reverse("clients:business_overview"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Business Overview")
        # The stacked bar renders with an absolute pixel height (not a
        # percentage that can collapse to 0 inside a flex item).
        self.assertContains(resp, "bo-seg")
        self.assertContains(resp, "220px")  # single product fills the plot height

    def test_business_overview_survives_garbage_params(self):
        resp = self.http.get(reverse("clients:business_overview"),
                             {"period": "banana", "columns": "-5"})
        self.assertEqual(resp.status_code, 200)

    def test_business_overview_forbidden_for_employee(self):
        u = User.objects.create_user(username="bo_emp", password="x")
        Employee.objects.create(user=u, role="employee", salary=0, active=True)
        http = TestClient()
        http.force_login(u)
        resp = http.get(reverse("clients:business_overview"))
        self.assertEqual(resp.status_code, 403)


class PasswordResetPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        admin = User.objects.create_user(username="pr_admin", password="x")
        Employee.objects.create(user=admin, role="admin", salary=0, active=True)
        target = User.objects.create_user(username="pr_target", password="x")
        cls.target_emp = Employee.objects.create(user=target, role="employee", salary=0, active=True)
        cls.admin = admin

    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.admin)

    def _reset(self, password):
        return self.http.post(
            reverse("clients:team_reset_password", args=[self.target_emp.id]),
            {"new_password": password},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_weak_passwords_rejected(self):
        for weak in ["123456", "password", "short1"]:
            resp = self._reset(weak)
            self.assertEqual(resp.status_code, 400, f"'{weak}' should be rejected")

    def test_strong_password_accepted(self):
        resp = self._reset("k9#Vip-Lantern42")
        self.assertEqual(resp.status_code, 200)


class LoginRedirectTests(TestCase):
    def test_authenticated_user_skips_login_page(self):
        user = User.objects.create_user(username="lr_emp", password="x")
        Employee.objects.create(user=user, role="employee", salary=0, active=True)
        http = TestClient()
        http.force_login(user)
        resp = http.get(reverse("clients:login"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("clients:employee_dashboard"))

    def test_anonymous_user_sees_login_page(self):
        resp = TestClient().get(reverse("clients:login"))
        self.assertEqual(resp.status_code, 200)


class ClientStatusSyncTests(TestCase):
    def test_reassign_updates_status_field(self):
        user = User.objects.create_user(username="cs_emp", password="x")
        emp = Employee.objects.create(user=user, role="employee", salary=0, active=True)
        client = Client.objects.create(name="Status Test")
        self.assertEqual(client.status, "Unmapped")

        client.reassign_to(emp)
        client.refresh_from_db()
        self.assertEqual(client.status, "Mapped")

        client.reassign_to(None)
        client.refresh_from_db()
        self.assertEqual(client.status, "Unmapped")

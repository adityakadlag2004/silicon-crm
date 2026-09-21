"""Tests for the security/robustness hardening batch: safe date parsing,
password validation, status sync.

Run: venv_new/bin/python manage.py test clients.test.test_hardening_batch -v 2
"""
from datetime import date

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee


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
        # Amounts render abbreviated (1000 → 1K) and the compartment is labelled.
        self.assertContains(resp, "bo-seg-lbl")
        self.assertContains(resp, "1K")

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


class DailyBusinessReportTests(TestCase):
    """The daily sheet is the monthly one scoped to a single date."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from datetime import date, timedelta
        from clients.models import Product, Sale
        user = User.objects.create_user(username="dbr_admin", password="x")
        cls.user = user
        emp = Employee.objects.create(user=user, role="admin", salary=0, active=True)
        Product.objects.get_or_create(name="SIP", defaults={"code": "SIP", "display_order": 1})
        c = Client.objects.create(name="DBR C")
        cls.day = date(2026, 5, 12)
        Sale.objects.create(client=c, employee=emp, product="SIP", amount=Decimal("1111"),
                            status="approved", date=cls.day)
        Sale.objects.create(client=c, employee=emp, product="SIP", amount=Decimal("2222"),
                            status="approved", date=cls.day + timedelta(days=1))

    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.user)

    def test_day_shows_only_that_days_sales(self):
        resp = self.http.get(reverse("clients:daily_business_report"), {"date": "2026-05-12"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1111")
        self.assertNotContains(resp, "2222")
        self.assertContains(resp, "12 May 2026")

    def test_garbage_date_falls_back_to_today(self):
        resp = self.http.get(reverse("clients:daily_business_report"), {"date": "banana"})
        self.assertEqual(resp.status_code, 200)

    def test_month_view_still_covers_the_whole_month(self):
        resp = self.http.get(reverse("clients:monthly_business_report"), {"month": 5, "year": 2026})
        self.assertContains(resp, "3333")

    # The app serves the same sheet through `business_report_sheet`, so these
    # pin the shaping the phone needs — not a second copy of the aggregation.

    def test_app_serves_the_same_day(self):
        resp = self.http.get(reverse("clients:app_report_daily"), {"date": "2026-05-12"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["date_label"], "12 May 2026")
        self.assertEqual(data["products"], ["SIP"])
        self.assertEqual(data["totals"]["values"], [1111.0])
        # One row per active employee, zeros included.
        self.assertEqual([r["name"] for r in data["rows"]], ["dbr_admin"])
        self.assertEqual(data["rows"][0]["values"], [1111.0])

    def test_app_hides_product_columns_with_no_business(self):
        """Seven columns on a 390dp screen are seven unreadable ones."""
        from clients.models import Product
        reportable = Product.objects.filter(
            is_active=True, parent__isnull=True, show_in_reports=True
        ).count()
        self.assertGreater(reportable, 1, "catalog has only the one product with business")
        data = self.http.get(reverse("clients:app_report_daily"), {"date": "2026-05-12"}).json()
        self.assertEqual(data["products"], ["SIP"])
        self.assertEqual(data["empty_columns"], reportable - 1)

    def test_app_garbage_date_falls_back_to_today(self):
        resp = self.http.get(reverse("clients:app_report_daily"), {"date": "banana"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["date"], date.today().isoformat())

    def test_app_daily_is_admin_or_manager_only(self):
        """The web page is; the phone is a real door onto the same figures."""
        other = User.objects.create_user(username="dbr_emp", password="x")
        Employee.objects.create(user=other, role="employee", salary=0, active=True)
        http = TestClient()
        http.force_login(other)
        self.assertEqual(http.get(reverse("clients:app_report_daily")).status_code, 403)


class ReportAccountsAndProductToggleTests(TestCase):
    """The business sheet counts accounts opened, and a product can be hidden."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from datetime import date
        from clients.models import Product, Sale
        user = User.objects.create_user(username="rat_admin", password="x")
        cls.user = user
        cls.emp = Employee.objects.create(user=user, role="admin", salary=0, active=True)
        cls.shown = Product.objects.create(name="Shown Prod", code="SHOWN", display_order=1)
        cls.hidden = Product.objects.create(name="Hidden Prod", code="HIDDEN", display_order=2,
                                            show_in_reports=False)
        c = Client.objects.create(name="RAT C", mapped_to=cls.emp)
        cls.day = date(2026, 4, 9)
        Sale.objects.create(client=c, employee=cls.emp, product="Shown Prod", product_ref=cls.shown,
                            amount=Decimal("4444"), status="approved", date=cls.day)
        Sale.objects.create(client=c, employee=cls.emp, product="Hidden Prod", product_ref=cls.hidden,
                            amount=Decimal("5555"), status="approved", date=cls.day)

    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.user)

    def _month(self):
        return self.http.get(reverse("clients:monthly_business_report"), {"month": 4, "year": 2026})

    def test_hidden_product_is_not_a_column(self):
        resp = self._month()
        self.assertContains(resp, "Shown Prod")
        self.assertNotContains(resp, "Hidden Prod")
        self.assertNotContains(resp, "5555")

    def test_accounts_opened_counted_for_the_period(self):
        from django.utils import timezone as tz
        from datetime import datetime
        c2 = Client.objects.create(name="RAT C2", mapped_to=self.emp)
        Client.objects.filter(pk__in=[c2.pk]).update(
            created_at=tz.make_aware(datetime(2026, 4, 9, 10, 0)))
        # The other client was created "now", so only one lands in April 2026.
        resp = self.http.get(reverse("clients:daily_business_report"), {"date": "2026-04-09"})
        self.assertContains(resp, "<th>Accounts</th>", html=False)
        self.assertEqual(resp.context["rows"][0]["accounts"], 1)
        self.assertEqual(resp.context["grand_accounts"], 1)

    def test_unmapped_client_credits_nobody(self):
        from django.utils import timezone as tz
        from datetime import datetime
        c3 = Client.objects.create(name="RAT C3")
        Client.objects.filter(pk=c3.pk).update(created_at=tz.make_aware(datetime(2026, 4, 9, 11, 0)))
        resp = self.http.get(reverse("clients:daily_business_report"), {"date": "2026-04-09"})
        self.assertEqual(resp.context["grand_accounts"], 0)

"""Tests for the native-app JSON API (clients/views/app_api.py).

Run: venv_new/bin/python manage.py test clients.test.test_app_api -v 2
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client, Employee, Sale


class AppDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="api_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="api_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.other_user = User.objects.create_user(username="api_other", password="x")
        cls.other = Employee.objects.create(user=cls.other_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="API Customer")

        today = timezone.localdate()
        Sale.objects.create(
            client=cls.customer, employee=cls.emp, product="SIP",
            amount=Decimal("5000"), status=Sale.STATUS_APPROVED, date=today,
        )
        Sale.objects.create(
            client=cls.customer, employee=cls.other, product="SIP",
            amount=Decimal("7000"), status=Sale.STATUS_PENDING, date=today,
        )

    def _get(self, user):
        http = TestClient()
        http.force_login(user)
        return http.get(reverse("clients:app_dashboard"))

    def test_requires_login(self):
        resp = TestClient().get(reverse("clients:app_dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_employee_sees_only_own_numbers(self):
        data = self._get(self.emp_user).json()
        self.assertEqual(data["role"], "employee")
        self.assertEqual(data["today"]["sales_count"], 1)
        self.assertEqual(data["today"]["amount"], 5000.0)
        # Recent list is scoped to own sales
        self.assertTrue(all(s["employee"] == "api_emp" for s in data["recent_sales"]))
        self.assertNotIn("pending_approvals", data)

    def test_admin_sees_firm_wide_and_approvals(self):
        data = self._get(self.admin_user).json()
        self.assertEqual(data["role"], "admin")
        self.assertEqual(data["today"]["amount"], 5000.0)  # approved only
        self.assertEqual(data["pending_approvals"], 1)
        self.assertEqual(len(data["recent_sales"]), 2)  # firm-wide list


class AppScreenApiTests(TestCase):
    """Covers the screen APIs added for the native migration (v3.1)."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="s_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="s_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Screen Customer", phone="9812345678", mapped_to=cls.emp)

        from clients.models import Product
        cls.product, _ = Product.objects.get_or_create(
            name="SIP", defaults={"code": "SIP"}
        )

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def test_sale_meta_employee_has_no_employee_list(self):
        data = self._http(self.emp_user).get(reverse("clients:app_sale_meta")).json()
        self.assertFalse(data["is_admin"])
        self.assertNotIn("employees", data)
        self.assertTrue(any(p["name"] == "SIP" for p in data["products"]))

    def test_sale_create_employee_is_pending_and_self_attributed(self):
        import json as _json
        resp = self._http(self.emp_user).post(
            reverse("clients:app_sale_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "amount": "5000", "employee_id": self.admin_emp.id,  # spoof attempt
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.status, Sale.STATUS_PENDING)
        self.assertEqual(sale.employee, self.emp)  # spoof ignored

    def test_sale_create_admin_auto_approves(self):
        import json as _json
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "amount": "9000", "employee_id": self.emp.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.json()["status"], "approved")
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.employee, self.emp)

    def test_clients_scopes(self):
        data = self._http(self.emp_user).get(reverse("clients:app_clients"), {"scope": "my"}).json()
        self.assertEqual(len(data["results"]), 1)
        data = self._http(self.emp_user).get(reverse("clients:app_clients"), {"q": "zzz-no-match"}).json()
        self.assertEqual(len(data["results"]), 0)

    def test_client_detail(self):
        data = self._http(self.emp_user).get(
            reverse("clients:app_client_detail", args=[self.customer.id])
        ).json()
        self.assertEqual(data["name"], "Screen Customer")

    def test_sales_list_scoped_and_approve_flow(self):
        import json as _json
        sale = Sale.objects.create(
            client=self.customer, employee=self.emp, product="SIP",
            amount=Decimal("1000"), status=Sale.STATUS_PENDING,
        )
        # Employee sees own, cannot approve
        data = self._http(self.emp_user).get(reverse("clients:app_sales")).json()
        self.assertFalse(data["can_approve"])
        self.assertTrue(all(r["employee"] for r in data["results"]))
        resp = self._http(self.emp_user).post(
            reverse("clients:app_sale_action", args=[sale.id]),
            data=_json.dumps({"action": "approve"}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        # Admin approves
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_action", args=[sale.id]),
            data=_json.dumps({"action": "approve"}), content_type="application/json",
        )
        self.assertEqual(resp.json()["status"], "approved")

    def test_followups_and_action(self):
        import json as _json
        from django.utils import timezone as tz
        from clients.models import CallFollowUp
        fu = CallFollowUp.objects.create(employee=self.emp, phone="123", scheduled_at=tz.now())
        data = self._http(self.emp_user).get(reverse("clients:app_followups")).json()
        self.assertEqual(len(data["pending"]), 1)
        resp = self._http(self.emp_user).post(
            reverse("clients:app_followup_action", args=[fu.id]),
            data=_json.dumps({"action": "done"}), content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        fu.refresh_from_db()
        self.assertEqual(fu.status, CallFollowUp.STATUS_DONE)

    def test_logout(self):
        http = self._http(self.emp_user)
        self.assertTrue(http.post(reverse("clients:app_logout")).json()["ok"])
        # Session is dead now
        resp = http.get(reverse("clients:app_dashboard"))
        self.assertEqual(resp.status_code, 302)


class AppRenewalNotificationTests(TestCase):
    """Screen APIs for v3.2: renewals + notifications."""

    @classmethod
    def setUpTestData(cls):
        cls.emp_user = User.objects.create_user(username="rn_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Renewal Customer", phone="9700000001")
        from clients.models import Product
        cls.product, _ = Product.objects.get_or_create(
            name="Life Insurance", defaults={"code": "LIFE_INS"}
        )

    def _http(self):
        c = TestClient()
        c.force_login(self.emp_user)
        return c

    def test_renewal_meta(self):
        data = self._http().get(reverse("clients:app_renewal_meta")).json()
        self.assertTrue(any(f["value"] == "yearly" for f in data["frequencies"]))
        self.assertNotIn("employees", data)

    def test_renewal_create_and_list(self):
        import json as _json
        resp = self._http().post(
            reverse("clients:app_renewal_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "premium_amount": "12000", "renewal_date": "2027-01-15",
                "frequency": "yearly", "notes": "test",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        from clients.models import Renewal
        r = Renewal.objects.get()
        self.assertEqual(r.employee, self.emp)
        self.assertEqual(r.product_type, Renewal.PRODUCT_TYPE_LIFE)

        data = self._http().get(reverse("clients:app_renewals")).json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["summary"]["month_count"], 1)  # collected today

    def test_renewal_create_bad_date_rejected(self):
        import json as _json
        resp = self._http().post(
            reverse("clients:app_renewal_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "premium_amount": "1000", "renewal_date": "15/01/2027", "frequency": "yearly",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_notifications_list_and_mark_read(self):
        from clients.models import Notification
        Notification.objects.create(recipient=self.emp_user, title="Hi", body="There")
        data = self._http().get(reverse("clients:app_notifications")).json()
        self.assertEqual(data["unread"], 1)
        self.assertEqual(len(data["results"]), 1)
        self._http().post(reverse("clients:app_notifications_read"))
        data = self._http().get(reverse("clients:app_notifications")).json()
        self.assertEqual(data["unread"], 0)

    def test_notifications_are_own_only(self):
        from clients.models import Notification
        other = User.objects.create_user(username="rn_other", password="x")
        Notification.objects.create(recipient=other, title="Secret", body="x")
        data = self._http().get(reverse("clients:app_notifications")).json()
        self.assertEqual(len(data["results"]), 0)


class AppLeadsReportsTests(TestCase):
    """Screen APIs for v3.3: leads pipeline + reports summary."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="lr_admin2", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="lr_emp2", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.other_user = User.objects.create_user(username="lr_other2", password="x")
        cls.other = Employee.objects.create(user=cls.other_user, role="employee", salary=0, active=True)

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def _post(self, user, url, payload):
        import json as _json
        return self._http(user).post(url, data=_json.dumps(payload), content_type="application/json")

    def test_lead_create_employee_forced_self(self):
        resp = self._post(self.emp_user, reverse("clients:app_lead_create"), {
            "customer_name": "Lead A", "phone": "981", "assigned_to_id": self.other.id,
        })
        self.assertEqual(resp.status_code, 200)
        from clients.models import Lead
        lead = Lead.objects.get()
        self.assertEqual(lead.assigned_to, self.emp)          # spoof ignored
        self.assertEqual(lead.progress_entries.count(), 3)    # seeded tracks

    def test_leads_scoped_to_employee(self):
        from clients.models import Lead
        Lead.objects.create(customer_name="Mine", assigned_to=self.emp)
        Lead.objects.create(customer_name="Theirs", assigned_to=self.other)
        data = self._http(self.emp_user).get(reverse("clients:app_leads")).json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["name"], "Mine")
        # Admin sees both
        data = self._http(self.admin_user).get(reverse("clients:app_leads")).json()
        self.assertEqual(len(data["results"]), 2)

    def test_progress_updates_stage_and_convert(self):
        from clients.models import Lead
        lead = Lead.objects.create(customer_name="Conv", phone="97000", assigned_to=self.emp)
        for product in ("health", "life", "wealth"):
            resp = self._post(
                self.emp_user,
                reverse("clients:app_lead_progress", args=[lead.id]),
                {"product": product, "status": "processed", "achieved_amount": "100000"},
            )
            self.assertEqual(resp.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.stage, Lead.STAGE_PROCESSED)

        resp = self._post(self.emp_user, reverse("clients:app_lead_action", args=[lead.id]), {"action": "convert"})
        self.assertEqual(resp.status_code, 200, resp.content)
        lead.refresh_from_db()
        client = lead.converted_client
        self.assertIsNotNone(client)
        self.assertTrue(client.health_status and client.life_status and client.sip_status)
        self.assertEqual(client.mapped_to, self.emp)

    def test_convert_requires_processed(self):
        from clients.models import Lead
        lead = Lead.objects.create(customer_name="Early", assigned_to=self.emp)
        resp = self._post(self.emp_user, reverse("clients:app_lead_action", args=[lead.id]), {"action": "convert"})
        self.assertEqual(resp.status_code, 400)

    def test_remark_added(self):
        from clients.models import Lead
        lead = Lead.objects.create(customer_name="R", assigned_to=self.emp)
        self._post(self.emp_user, reverse("clients:app_lead_remark", args=[lead.id]), {"text": "called, callback tomorrow"})
        self.assertEqual(lead.remarks.count(), 1)

    def test_report_summary_scoping(self):
        from django.utils import timezone as tz
        c = Client.objects.create(name="Rep C")
        Sale.objects.create(client=c, employee=self.emp, product="SIP",
                            amount=Decimal("1000"), status=Sale.STATUS_APPROVED, date=tz.localdate())
        Sale.objects.create(client=c, employee=self.other, product="SIP",
                            amount=Decimal("2000"), status=Sale.STATUS_APPROVED, date=tz.localdate())

        data = self._http(self.emp_user).get(reverse("clients:app_report_summary")).json()
        self.assertFalse(data["firm_wide"])
        self.assertNotIn("leaderboard", data)
        self.assertEqual(data["trend"][-1]["amount"], 1000.0)  # own only

        data = self._http(self.admin_user).get(reverse("clients:app_report_summary")).json()
        self.assertTrue(data["firm_wide"])
        self.assertEqual(data["trend"][-1]["amount"], 3000.0)
        self.assertEqual(len(data["leaderboard"]), 2)


class DeviceStatusTests(TestCase):
    def test_report_and_upsert(self):
        import json as _json
        user = User.objects.create_user(username="ds_emp", password="x")
        Employee.objects.create(user=user, role="employee", salary=0, active=True)
        http = TestClient()
        http.force_login(user)
        resp = http.post(
            reverse("clients:app_device_status"),
            data=_json.dumps({"calls_granted": True, "overlay_granted": False,
                              "notifications_granted": True, "app_version": "3.3.1"}),
            content_type="application/json",
        )
        self.assertTrue(resp.json()["ok"])
        from clients.models import AppDeviceStatus
        s = AppDeviceStatus.objects.get(user=user)
        self.assertTrue(s.calls_granted)
        self.assertFalse(s.overlay_granted)
        # Second report updates, not duplicates
        http.post(
            reverse("clients:app_device_status"),
            data=_json.dumps({"calls_granted": True, "overlay_granted": True,
                              "notifications_granted": True, "app_version": "3.3.1"}),
            content_type="application/json",
        )
        self.assertEqual(AppDeviceStatus.objects.filter(user=user).count(), 1)
        s.refresh_from_db()
        self.assertTrue(s.overlay_granted)

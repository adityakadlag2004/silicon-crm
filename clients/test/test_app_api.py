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


class AppUpdateEndpointTests(TestCase):
    def test_version_unavailable_when_unpublished(self):
        import tempfile
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(MEDIA_ROOT=tmp):
                data = TestClient().get(reverse("clients:app_version")).json()
                self.assertFalse(data["available"])
                resp = TestClient().get(reverse("clients:app_apk_download"))
                self.assertEqual(resp.status_code, 404)

    def test_version_and_download_when_published(self):
        import json as _json
        import os
        import tempfile
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "app"))
            with open(os.path.join(tmp, "app", "version.json"), "w") as f:
                _json.dump({"version_code": 12, "version_name": "3.4.0", "notes": "test"}, f)
            with open(os.path.join(tmp, "app", "latest.apk"), "wb") as f:
                f.write(b"fake-apk-bytes")
            with override_settings(MEDIA_ROOT=tmp):
                data = TestClient().get(reverse("clients:app_version")).json()
                self.assertTrue(data["available"])
                self.assertEqual(data["version_code"], 12)
                self.assertIn("/clients/app/latest.apk", data["url"])
                resp = TestClient().get(reverse("clients:app_apk_download"))
                self.assertEqual(resp.status_code, 200)


class AppTeamApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="t_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="t_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def _post(self, user, url, payload):
        import json as _json
        return self._http(user).post(url, data=_json.dumps(payload), content_type="application/json")

    def test_admin_only(self):
        self.assertEqual(self._http(self.emp_user).get(reverse("clients:app_team")).status_code, 403)
        self.assertEqual(self._http(self.admin_user).get(reverse("clients:app_team")).status_code, 200)

    def test_create_validates_password_and_role(self):
        resp = self._post(self.admin_user, reverse("clients:app_team_create"), {
            "username": "newguy", "password": "123456", "role": "employee",
        })
        self.assertEqual(resp.status_code, 400)  # weak password
        resp = self._post(self.admin_user, reverse("clients:app_team_create"), {
            "username": "newguy", "password": "k9#Vip-Lantern42", "role": "superboss",
        })
        self.assertEqual(resp.status_code, 400)  # bogus role
        resp = self._post(self.admin_user, reverse("clients:app_team_create"), {
            "username": "newguy", "password": "k9#Vip-Lantern42", "role": "employee", "salary": "15000",
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        e = Employee.objects.get(user__username="newguy")
        self.assertTrue(e.employee_number)  # auto-assigned

    def test_update_role_audited(self):
        resp = self._post(self.admin_user, reverse("clients:app_team_update", args=[self.emp.id]), {
            "role": "manager", "salary": "0",
        })
        self.assertEqual(resp.status_code, 200)
        from clients.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_EMPLOYEE_ROLE_CHANGED, target_id=self.emp.pk
        ).exists())

    def test_toggle_reassigns_clients(self):
        c = Client.objects.create(name="Toggle C", mapped_to=self.emp)
        resp = self._post(self.admin_user, reverse("clients:app_team_toggle", args=[self.emp.id]), {})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["active"])
        self.assertEqual(data["reassigned"], 1)
        c.refresh_from_db()
        self.assertEqual(c.mapped_to, self.admin_emp)
        self.emp.user.refresh_from_db()
        self.assertFalse(self.emp.user.is_active)

    def test_reset_password_policy(self):
        resp = self._post(self.admin_user, reverse("clients:app_team_reset_password", args=[self.emp.id]), {
            "new_password": "123456",
        })
        self.assertEqual(resp.status_code, 400)
        resp = self._post(self.admin_user, reverse("clients:app_team_reset_password", args=[self.emp.id]), {
            "new_password": "k9#Vip-Lantern42",
        })
        self.assertEqual(resp.status_code, 200)


class AppClientCreateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="cc_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="cc_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)

    def _post(self, user, payload):
        import json as _json
        c = TestClient()
        c.force_login(user)
        return c.post(reverse("clients:app_client_create"),
                      data=_json.dumps(payload), content_type="application/json")

    def test_employee_client_maps_to_self(self):
        resp = self._post(self.emp_user, {"name": "New Client", "phone": "9811112222"})
        self.assertEqual(resp.status_code, 200, resp.content)
        c = Client.objects.get(name="New Client")
        self.assertEqual(c.mapped_to, self.emp)
        self.assertEqual(c.status, "Mapped")

    def test_requires_name_and_phone(self):
        self.assertEqual(self._post(self.emp_user, {"name": "X"}).status_code, 400)
        self.assertEqual(self._post(self.emp_user, {"phone": "981"}).status_code, 400)

    def test_duplicate_phone_rejected(self):
        Client.objects.create(name="Existing", phone="9822223333")
        resp = self._post(self.emp_user, {"name": "Dup", "phone": "+91 98222 23333"})
        self.assertEqual(resp.status_code, 400)

    def test_admin_can_leave_unmapped_or_assign(self):
        resp = self._post(self.admin_user, {"name": "Unmapped C", "phone": "9833334444", "mapped_to_id": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(Client.objects.get(name="Unmapped C").mapped_to)
        resp = self._post(self.admin_user, {"name": "Assigned C", "phone": "9844445555", "mapped_to_id": self.emp.id})
        self.assertEqual(Client.objects.get(name="Assigned C").mapped_to, self.emp)


class AppIncentivesCampaignsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="ic_admin", password="x")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="ic_emp", password="x")
        Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def test_admin_only(self):
        for name in ("clients:app_incentives", "clients:app_campaigns"):
            self.assertEqual(self._http(self.emp_user).get(reverse(name)).status_code, 403)
            self.assertEqual(self._http(self.admin_user).get(reverse(name)).status_code, 200)

    def test_snapshots_shape(self):
        from clients.models import Campaign, CampaignProduct, IncentiveRule, IncentiveSlab, Product
        from datetime import date as _d, timedelta as _td
        p, _ = Product.objects.get_or_create(name="PMS", defaults={"code": "PMS"})
        rule = IncentiveRule.objects.create(product=p.name, product_ref=p,
                                            unit_amount=Decimal("100000"), points_per_unit=Decimal("2"))
        IncentiveSlab.objects.create(rule=rule, threshold=Decimal("500000"), payout=Decimal("100"))
        camp = Campaign.objects.create(name="Diwali", start_date=_d.today(),
                                       end_date=_d.today() + _td(days=30))
        CampaignProduct.objects.create(campaign=camp, product_ref=p,
                                       benefit_type="unit", unit_amount=Decimal("1000"),
                                       points_per_unit=Decimal("1.5"))

        data = self._http(self.admin_user).get(reverse("clients:app_incentives")).json()
        self.assertEqual(len(data["rules"]), 1)
        self.assertEqual(len(data["rules"][0]["slabs"]), 1)
        self.assertTrue(all(pr["name"] != "PMS" for pr in data["available_products"]))

        data = self._http(self.admin_user).get(reverse("clients:app_campaigns")).json()
        self.assertEqual(len(data["campaigns"]), 1)
        self.assertEqual(data["campaigns"][0]["products"][0]["product_name"], "PMS")


class AppSheetsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from clients.models import LeadSheet, LeadSheetColumn
        cls.owner_user = User.objects.create_user(username="sh_owner", password="x")
        cls.owner = Employee.objects.create(user=cls.owner_user, role="employee", salary=0, active=True)
        cls.outsider_user = User.objects.create_user(username="sh_out", password="x")
        cls.outsider = Employee.objects.create(user=cls.outsider_user, role="employee", salary=0, active=True)
        cls.sheet = LeadSheet.objects.create(name="Health Q3", owner=cls.owner, is_private=True)
        LeadSheetColumn.objects.create(sheet=cls.sheet, name="Name", field_key="name",
                                       type=LeadSheetColumn.TYPE_TEXT, required=True, display_order=0)
        LeadSheetColumn.objects.create(sheet=cls.sheet, name="Status", field_key="status",
                                       type=LeadSheetColumn.TYPE_STATUS, options=["new", "hot"], display_order=1)

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def test_private_sheet_hidden_from_outsider(self):
        data = self._http(self.outsider_user).get(reverse("clients:app_sheets")).json()
        self.assertEqual(len(data["results"]), 0)
        resp = self._http(self.outsider_user).get(reverse("clients:app_sheet_records", args=[self.sheet.id]))
        self.assertEqual(resp.status_code, 403)

    def test_owner_sees_columns_and_can_add_row(self):
        import json as _json
        data = self._http(self.owner_user).get(reverse("clients:app_sheet_records", args=[self.sheet.id])).json()
        self.assertEqual(len(data["columns"]), 2)
        self.assertTrue(data["sheet"]["can_edit"])
        resp = self._http(self.owner_user).post(
            reverse("clients:app_sheet_record_save", args=[self.sheet.id]),
            data=_json.dumps({"values": {"name": "Ramesh", "status": "hot"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        from clients.models import LeadSheetRecord
        rec = LeadSheetRecord.objects.get(sheet=self.sheet)
        self.assertEqual(rec.values["name"], "Ramesh")

    def test_required_field_enforced(self):
        import json as _json
        resp = self._http(self.owner_user).post(
            reverse("clients:app_sheet_record_save", args=[self.sheet.id]),
            data=_json.dumps({"values": {"status": "new"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class AppLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="li_emp", password="rightpass123!")
        Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)

    def setUp(self):
        # LocMemCache is process-global; clear the login-lockout counter so
        # tests don't pollute each other.
        from django.core.cache import cache
        cache.clear()

    def _login(self, username, password):
        import json as _json
        return TestClient().post(
            reverse("clients:app_login"),
            data=_json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )

    def test_success_returns_role_and_sets_session(self):
        resp = self._login("li_emp", "rightpass123!")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["role"], "employee")
        self.assertIn("sessionid", resp.cookies)

    def test_wrong_password_401(self):
        resp = self._login("li_emp", "wrong")
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.json()["ok"])

    def test_lockout_after_five_failures(self):
        from django.core.cache import cache
        cache.clear()
        for _ in range(5):
            self._login("li_emp", "wrong")
        resp = self._login("li_emp", "rightpass123!")  # correct, but locked out
        self.assertEqual(resp.status_code, 429)

    def test_user_without_role_rejected(self):
        User.objects.create_user(username="noemp", password="rightpass123!")
        resp = self._login("noemp", "rightpass123!")
        self.assertEqual(resp.status_code, 403)


class AppFollowupStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp_user = User.objects.create_user(username="fs_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)

    def _http(self):
        c = TestClient()
        c.force_login(self.emp_user)
        return c

    def test_stats_and_pending_only(self):
        from clients.models import CallLogEntry, CallFollowUp
        base = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        # 3 calls today in work hours: 2 connected (one serious 200s, one 60s), 1 missed
        CallLogEntry.objects.create(employee=self.emp, phone="1", direction="outgoing",
                                    connected=True, duration_seconds=200, started_at=base)
        CallLogEntry.objects.create(employee=self.emp, phone="2", direction="outgoing",
                                    connected=True, duration_seconds=60, started_at=base)
        CallLogEntry.objects.create(employee=self.emp, phone="3", direction="incoming",
                                    connected=False, duration_seconds=0, started_at=base)
        # After-hours call must NOT count
        CallLogEntry.objects.create(employee=self.emp, phone="4", direction="outgoing",
                                    connected=True, duration_seconds=300,
                                    started_at=base.replace(hour=21))

        # A done follow-up must not appear
        CallFollowUp.objects.create(employee=self.emp, phone="9", scheduled_at=timezone.now(),
                                    status=CallFollowUp.STATUS_DONE)
        CallFollowUp.objects.create(employee=self.emp, phone="8", scheduled_at=timezone.now())

        data = self._http().get(reverse("clients:app_followups")).json()
        s = data["stats"]
        self.assertEqual(s["calls"], 3)          # after-hours excluded
        self.assertEqual(s["connected"], 2)
        self.assertEqual(s["serious"], 1)        # only the 200s call
        self.assertAlmostEqual(s["talk_minutes"], round(260 / 60, 1))
        self.assertEqual(len(data["pending"]), 1)  # done one excluded
        self.assertNotIn("done", data)

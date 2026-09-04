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
        # The dashboard must not ship a payload no screen renders — the
        # unused `recent_sales` list was cut; Menu → All Sales covers it.
        self.assertNotIn("recent_sales", data)
        self.assertNotIn("pending_approvals", data)

    def test_admin_sees_firm_wide_and_approvals(self):
        data = self._get(self.admin_user).json()
        self.assertEqual(data["role"], "admin")
        self.assertEqual(data["today"]["amount"], 5000.0)  # approved only
        self.assertEqual(data["pending_approvals"], 1)
        self.assertNotIn("recent_sales", data)
        # New admin home widgets
        self.assertIn("leaderboard_today", data)
        self.assertIn("product_mtd", data)
        self.assertIn("team_calls_today", data)
        self.assertEqual(sum(p["amount"] for p in data["product_mtd"]), 5000.0)  # only approved
        self.assertTrue(any(r["name"] for r in data["leaderboard_today"]))


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

    def test_sale_meta_employee_gets_the_employee_list(self):
        # The picker is for everyone now, so the list must reach a non-admin —
        # but the commission figures next to it must not.
        data = self._http(self.emp_user).get(reverse("clients:app_sale_meta")).json()
        self.assertFalse(data["is_admin"])
        self.assertIn("employees", data)
        self.assertNotIn("ppt_fyc", data)
        self.assertTrue(any(p["name"] == "SIP" for p in data["products"]))

    def test_sale_create_employee_can_attribute_but_still_pends(self):
        import json as _json
        resp = self._http(self.emp_user).post(
            reverse("clients:app_sale_create"),
            data=_json.dumps({
                "client_id": self.customer.id, "product_id": self.product.id,
                "amount": "5000", "employee_id": self.admin_emp.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        sale = Sale.objects.latest("id")
        self.assertEqual(sale.employee, self.admin_emp)
        self.assertEqual(sale.status, Sale.STATUS_PENDING)  # never self-approving

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

    def test_admin_can_delete_sale(self):
        import json as _json
        sale = Sale.objects.create(
            client=self.customer, employee=self.emp, product="SIP",
            amount=Decimal("1000"), status=Sale.STATUS_PENDING,
        )
        # employee cannot delete
        resp = self._http(self.emp_user).post(
            reverse("clients:app_sale_action", args=[sale.id]),
            data=_json.dumps({"action": "delete"}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        # admin can delete
        resp = self._http(self.admin_user).post(
            reverse("clients:app_sale_action", args=[sale.id]),
            data=_json.dumps({"action": "delete"}), content_type="application/json",
        )
        self.assertTrue(resp.json()["deleted"])
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())

    def test_sales_list_exposes_can_delete(self):
        admin_data = self._http(self.admin_user).get(reverse("clients:app_sales")).json()
        self.assertTrue(admin_data["can_delete"])
        emp_data = self._http(self.emp_user).get(reverse("clients:app_sales")).json()
        self.assertFalse(emp_data["can_delete"])

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
        # Epoch millis drive the app's exact on-device alarms.
        self.assertEqual(
            data["pending"][0]["scheduled_at_ms"], int(fu.scheduled_at.timestamp() * 1000)
        )
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
        # No product rows are invented for a lead nobody has qualified yet.
        self.assertEqual(lead.interests.count(), 0)
        self.assertEqual(lead.stage, Lead.STAGE_SUSPECT)

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

    def test_stage_moves_are_logged_and_convert_needs_order(self):
        from clients.models import Lead
        lead = Lead.objects.create(customer_name="Conv", phone="97000", assigned_to=self.emp)
        for stage in Lead.STAGE_SEQUENCE[1:]:
            resp = self._post(
                self.emp_user,
                reverse("clients:app_lead_stage", args=[lead.id]),
                {"stage": stage, "note": f"moved to {stage}"},
            )
            self.assertEqual(resp.status_code, 200, resp.content)
        lead.refresh_from_db()
        self.assertEqual(lead.stage, Lead.STAGE_ORDER)
        self.assertEqual(lead.stage_events.count(), len(Lead.STAGE_SEQUENCE) - 1)

        resp = self._post(self.emp_user, reverse("clients:app_lead_action", args=[lead.id]), {"action": "convert"})
        self.assertEqual(resp.status_code, 200, resp.content)
        lead.refresh_from_db()
        self.assertIsNotNone(lead.converted_client)
        self.assertEqual(lead.converted_client.mapped_to, self.emp)

    def test_convert_requires_order_stage(self):
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

    def test_report_summary_product_bifurcation(self):
        from django.utils import timezone as tz
        from clients.models import Product
        Product.objects.get_or_create(name="SIP", defaults={"code": "SIP", "display_order": 1})
        Product.objects.get_or_create(name="Health", defaults={"code": "HLT", "display_order": 2})
        c = Client.objects.create(name="Bif C")
        today = tz.localdate()
        Sale.objects.create(client=c, employee=self.emp, product="SIP", amount=Decimal("2000"),
                            status=Sale.STATUS_APPROVED, date=today)
        Sale.objects.create(client=c, employee=self.emp, product="Health", amount=Decimal("3000"),
                            status=Sale.STATUS_APPROVED, date=today)

        data = self._http(self.admin_user).get(reverse("clients:app_report_summary")).json()
        self.assertIn("SIP", data["buckets"])
        self.assertIn("Health", data["buckets"])
        sip_i = data["buckets"].index("SIP")
        health_i = data["buckets"].index("Health")

        last = data["trend"][-1]
        self.assertEqual(last["amount"], 5000.0)
        self.assertEqual(last["by_product"][sip_i], 2000.0)
        self.assertEqual(last["by_product"][health_i], 3000.0)

        # Leaderboard row carries the same product split.
        emp_row = next(e for e in data["leaderboard"] if e["amount"] == 5000.0)
        self.assertEqual(emp_row["by_product"][sip_i], 2000.0)
        self.assertEqual(emp_row["by_product"][health_i], 3000.0)

    def test_report_summary_period_and_columns(self):
        data = self._http(self.admin_user).get(
            reverse("clients:app_report_summary") + "?period=quarter&columns=4"
        ).json()
        self.assertEqual(data["period"], "quarter")
        self.assertEqual(data["columns"], 4)
        self.assertEqual(len(data["trend"]), 4)

        # Invalid params fall back to sane defaults / clamp.
        data = self._http(self.admin_user).get(
            reverse("clients:app_report_summary") + "?period=weekly&columns=999"
        ).json()
        self.assertEqual(data["period"], "month")
        self.assertEqual(data["columns"], 24)  # clamped to MAX_COLUMNS
        self.assertEqual(len(data["trend"]), 24)


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

    def test_diagnostics_stored_and_bad_shape_ignored(self):
        import json as _json
        user = User.objects.create_user(username="ds_diag", password="x")
        Employee.objects.create(user=user, role="employee", salary=0, active=True)
        http = TestClient()
        http.force_login(user)
        diag = {"popup_enabled": True, "sim_configured": False,
                "last_popup_result": "shown", "exact_alarms": True}
        http.post(
            reverse("clients:app_device_status"),
            data=_json.dumps({"calls_granted": True, "app_version": "4.12.0",
                              "diagnostics": diag}),
            content_type="application/json",
        )
        from clients.models import AppDeviceStatus
        s = AppDeviceStatus.objects.get(user=user)
        self.assertEqual(s.diagnostics["last_popup_result"], "shown")
        self.assertFalse(s.diagnostics["sim_configured"])
        # Non-dict diagnostics (old app version sends none) → stored as {}
        http.post(
            reverse("clients:app_device_status"),
            data=_json.dumps({"calls_granted": True, "diagnostics": "garbage"}),
            content_type="application/json",
        )
        s.refresh_from_db()
        self.assertEqual(s.diagnostics, {})


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
        # Mandatory on a new client since Sep 2026; these cases are about PAN
        # and mapping, so they get a valid one unless they say otherwise.
        payload = {"date_of_birth": "1990-04-02", **payload}
        return c.post(reverse("clients:app_client_create"),
                      data=_json.dumps(payload), content_type="application/json")

    def test_employee_client_maps_to_self(self):
        resp = self._post(self.emp_user, {"name": "New Client", "phone": "9811112222", "pan": "ABCDE1234F"})
        self.assertEqual(resp.status_code, 200, resp.content)
        c = Client.objects.get(name="New Client")
        self.assertEqual(c.mapped_to, self.emp)
        self.assertEqual(c.status, "Mapped")
        self.assertEqual(c.pan, "ABCDE1234F")

    def test_requires_name_and_phone(self):
        self.assertEqual(self._post(self.emp_user, {"name": "X"}).status_code, 400)
        self.assertEqual(self._post(self.emp_user, {"phone": "981"}).status_code, 400)

    def test_requires_pan(self):
        # PAN links the client to their MF folios — mandatory on create.
        self.assertEqual(self._post(self.emp_user, {"name": "NoPan", "phone": "9811119999"}).status_code, 400)
        self.assertEqual(self._post(self.emp_user, {"name": "BadPan", "phone": "9811119999", "pan": "XYZ"}).status_code, 400)

    def test_duplicate_phone_rejected(self):
        Client.objects.create(name="Existing", phone="9822223333")
        resp = self._post(self.emp_user, {"name": "Dup", "phone": "+91 98222 23333", "pan": "ABCDE1234F"})
        self.assertEqual(resp.status_code, 400)

    def test_admin_can_leave_unmapped_or_assign(self):
        resp = self._post(self.admin_user, {"name": "Unmapped C", "phone": "9833334444", "mapped_to_id": "", "pan": "ABCDE1234F"})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(Client.objects.get(name="Unmapped C").mapped_to)
        resp = self._post(self.admin_user, {"name": "Assigned C", "phone": "9844445555", "mapped_to_id": self.emp.id, "pan": "ABCDE1234G"})
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


class AppSheetsRemovedTests(TestCase):
    """Lead Records was removed; the app endpoints stay as graceful stubs
    until every device runs an app version without the Sheets screen."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sh_emp", password="x")
        Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)

    def _http(self):
        c = TestClient()
        c.force_login(self.user)
        return c

    def test_sheets_list_is_empty(self):
        data = self._http().get(reverse("clients:app_sheets")).json()
        self.assertEqual(data["results"], [])

    def test_sheet_records_gone(self):
        resp = self._http().get(reverse("clients:app_sheet_records", args=[1]))
        self.assertEqual(resp.status_code, 410)

    def test_sheet_record_save_gone(self):
        resp = self._http().post(reverse("clients:app_sheet_record_save", args=[1]))
        self.assertEqual(resp.status_code, 410)


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
        from clients.models import CallTrackingSettings
        CallTrackingSettings.objects.update_or_create(pk=1, defaults={"work_days": "0,1,2,3,4,5,6"})

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
        # "calls"/"connected" are outgoing-only (Dialed), matching Call
        # Analytics — the incoming missed call is excluded, as is after-hours.
        self.assertEqual(s["calls"], 2)
        self.assertEqual(s["connected"], 2)
        self.assertEqual(s["serious"], 1)        # only the 200s call
        self.assertAlmostEqual(s["talk_minutes"], round(260 / 60, 1))
        self.assertEqual(len(data["pending"]), 1)  # done one excluded
        self.assertNotIn("done", data)


class AppReportsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user(username="rp_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="rp_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Rep Cust")
        today = timezone.localdate()
        Sale.objects.create(client=cls.customer, employee=cls.emp, product="SIP",
                            amount=Decimal("5000"), status=Sale.STATUS_APPROVED, date=today)
        Sale.objects.create(client=cls.customer, employee=cls.admin_emp, product="PMS",
                            amount=Decimal("9000"), status=Sale.STATUS_APPROVED, date=today)

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def test_monthly_admin_firm_wide_with_employees(self):
        data = self._http(self.admin_user).get(reverse("clients:app_report_monthly")).json()
        self.assertTrue(data["firm_wide"])
        self.assertEqual(data["total_amount"], 14000.0)
        self.assertIn("employees", data)
        self.assertEqual({p["name"] for p in data["products"]}, {"SIP", "PMS"})

    def test_monthly_employee_scoped_no_employees_list(self):
        data = self._http(self.emp_user).get(reverse("clients:app_report_monthly")).json()
        self.assertFalse(data["firm_wide"])
        self.assertEqual(data["total_amount"], 5000.0)   # own only
        self.assertNotIn("employees", data)

    def test_summary_drills_down_to_one_employee(self):
        """The leaderboard row carries an id, and passing it back retells the
        whole report for that person — what the old Past Performance screen did
        with a dropdown."""
        url = reverse("clients:app_report_summary")
        data = self._http(self.admin_user).get(url, {"period": "month", "columns": 12}).json()
        self.assertEqual(len(data["trend"]), 12)
        self.assertTrue(data["firm_wide"])
        self.assertEqual(data["scope_name"], "Whole firm")
        row = next(e for e in data["leaderboard"] if e["employee_id"] == self.emp.id)

        one = self._http(self.admin_user).get(url, {"employee_id": row["employee_id"]}).json()
        self.assertEqual(one["employee_id"], self.emp.id)
        self.assertEqual(sum(t["amount"] for t in one["trend"]), 5000.0)

    def test_summary_select_moves_the_mix_off_the_latest_period(self):
        url = reverse("clients:app_report_summary")
        data = self._http(self.admin_user).get(url, {"select": 1}).json()
        self.assertEqual(data["select"], 1)
        self.assertLess(data["current_start"], data["current_end"])
        latest = self._http(self.admin_user).get(url).json()
        self.assertEqual(latest["select"], 0)
        self.assertNotEqual(data["current_start"], latest["current_start"])

    def test_summary_lists_employees_to_pick_from(self):
        """The Employee Performance screen opens on this list, so it must carry
        everyone active — including whoever sold nothing in the window."""
        idle = Employee.objects.create(
            user=User.objects.create_user("idle_seller"), role="employee", salary=0, active=True,
        )
        data = self._http(self.admin_user).get(reverse("clients:app_report_summary")).json()
        ids = {e["id"] for e in data["employees"]}
        self.assertIn(self.emp.id, ids)
        self.assertIn(idle.id, ids)          # no sales, still pickable

    def test_summary_employee_sees_own_and_cannot_drill(self):
        data = self._http(self.emp_user).get(
            reverse("clients:app_report_summary"), {"employee_id": 999999},
        ).json()
        self.assertFalse(data["firm_wide"])
        self.assertIsNone(data["employee_id"])
        self.assertNotIn("leaderboard", data)


class AppEmployeeGamificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal as D
        cls.emp_user = User.objects.create_user(username="gm_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=D("10000"), active=True)
        cls.customer = Client.objects.create(name="Gm Cust")

    def _dash(self):
        c = TestClient(); c.force_login(self.emp_user)
        return c.get(reverse("clients:app_dashboard")).json()

    def test_earnings_below_salary(self):
        from decimal import Decimal as D
        s = Sale.objects.create(client=self.customer, employee=self.emp, product="SIP",
                                amount=D("1000"), status=Sale.STATUS_APPROVED, date=timezone.localdate())
        Sale.objects.filter(pk=s.pk).update(points=D("4000"))  # below 10000 salary
        e = self._dash()["earnings"]
        self.assertEqual(e["salary"], 10000.0)
        self.assertEqual(e["earned"], 4000.0)
        self.assertFalse(e["justified"])
        self.assertEqual(e["remaining"], 6000.0)

    def test_earnings_justified(self):
        from decimal import Decimal as D
        s = Sale.objects.create(client=self.customer, employee=self.emp, product="SIP",
                                amount=D("1000"), status=Sale.STATUS_APPROVED, date=timezone.localdate())
        Sale.objects.filter(pk=s.pk).update(points=D("12000"))  # above salary
        e = self._dash()["earnings"]
        self.assertTrue(e["justified"])
        self.assertEqual(e["surplus"], 2000.0)

    def test_active_campaign_shown(self):
        from decimal import Decimal as D
        from datetime import timedelta as td
        from clients.models import Campaign, CampaignProduct, Product
        p, _ = Product.objects.get_or_create(name="SIP", defaults={"code": "SIP"})
        today = timezone.localdate()
        camp = Campaign.objects.create(name="Diwali Blast", start_date=today - td(days=1),
                                       end_date=today + td(days=10), is_active=True)
        CampaignProduct.objects.create(campaign=camp, product_ref=p, benefit_type="unit",
                                       unit_amount=D("1000"), points_per_unit=D("2"))
        data = self._dash()
        self.assertEqual(len(data["active_campaigns"]), 1)
        self.assertEqual(data["active_campaigns"][0]["name"], "Diwali Blast")

    def test_admin_has_no_earnings(self):
        admin = User.objects.create_user(username="gm_admin", password="x")
        Employee.objects.create(user=admin, role="admin", salary=0, active=True)
        c = TestClient(); c.force_login(admin)
        data = c.get(reverse("clients:app_dashboard")).json()
        self.assertNotIn("earnings", data)


class AppMeAndNotificationTests(TestCase):
    """The cheap identity endpoint (four screens used to pull the whole
    dashboard for one `role` string) and per-notification read."""

    @classmethod
    def setUpTestData(cls):
        from clients.models import Notification
        cls.user = User.objects.create_user("me_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)
        cls.n1 = Notification.objects.create(recipient=cls.user, title="One", body="a")
        cls.n2 = Notification.objects.create(recipient=cls.user, title="Two", body="b")

    def _http(self):
        c = TestClient()
        c.force_login(self.user)
        return c

    def test_me_returns_role_and_unread_count(self):
        data = self._http().get(reverse("clients:app_me")).json()
        self.assertEqual(data["role"], "employee")
        self.assertEqual(data["unread_notifications"], 2)
        self.assertEqual(data["employee_id"], self.emp.id)

    def test_me_requires_login(self):
        self.assertEqual(TestClient().get(reverse("clients:app_me")).status_code, 302)

    def test_reading_one_notification_leaves_the_others(self):
        import json as _json
        from clients.models import Notification
        resp = self._http().post(
            reverse("clients:app_notifications_read"),
            data=_json.dumps({"id": self.n1.id}), content_type="application/json",
        )
        self.assertEqual(resp.json()["unread"], 1)
        self.assertTrue(Notification.objects.get(pk=self.n1.id).is_read)
        self.assertFalse(Notification.objects.get(pk=self.n2.id).is_read)

    def test_reading_with_no_id_still_marks_all(self):
        import json as _json
        resp = self._http().post(
            reverse("clients:app_notifications_read"),
            data=_json.dumps({}), content_type="application/json",
        )
        self.assertEqual(resp.json()["unread"], 0)


class AppTaskPaginationTests(TestCase):
    """The task list used to be a silent qs[:200] truncation."""

    @classmethod
    def setUpTestData(cls):
        from clients.models import Task
        cls.user = User.objects.create_user("pg_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)
        for i in range(55):
            Task.objects.create(title=f"T{i}", created_by=cls.user, assigned_to=cls.emp)

    def _get(self, **params):
        c = TestClient()
        c.force_login(self.user)
        return c.get(reverse("clients:app_tasks"), {"tab": "my", **params}).json()

    def test_first_page_is_capped_and_flags_more(self):
        data = self._get()
        self.assertEqual(len(data["tasks"]), 50)
        self.assertTrue(data["has_more"])

    def test_second_page_returns_the_tail(self):
        data = self._get(page=2)
        self.assertEqual(len(data["tasks"]), 5)
        self.assertFalse(data["has_more"])

    def test_pages_do_not_overlap(self):
        first = {t["id"] for t in self._get()["tasks"]}
        second = {t["id"] for t in self._get(page=2)["tasks"]}
        self.assertEqual(first & second, set())


class AppTaskWindowTests(TestCase):
    """The screen's day/week/month picker used to filter the scorecard only,
    so the list showed every task ever created."""

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta
        from clients.models import Task
        cls.user = User.objects.create_user("win_emp", password="x")
        cls.emp = Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)
        today = timezone.localdate()

        def mk(title, due, age_days, status=Task.STATUS_COMPLETED):
            t = Task.objects.create(title=title, created_by=cls.user,
                                    assigned_to=cls.emp, due_date=due, status=status)
            Task.objects.filter(pk=t.pk).update(
                created_at=timezone.now() - timedelta(days=age_days))
            return t

        mk("old done", today - timedelta(days=40), 40)
        mk("old overdue", today - timedelta(days=40), 40, Task.STATUS_PENDING)
        mk("due tomorrow", today + timedelta(days=1), 60, Task.STATUS_PENDING)
        mk("undated new", None, 0)
        mk("undated old", None, 40)

    def _titles(self, window):
        c = TestClient()
        c.force_login(self.user)
        data = c.get(reverse("clients:app_tasks"), {"tab": "my", "window": window}).json()
        return {t["title"] for t in data["tasks"]}

    def test_week_drops_the_old_tail_but_keeps_upcoming_work(self):
        self.assertEqual(self._titles("week"),
                         {"due tomorrow", "undated new", "old overdue"})

    def test_an_open_overdue_task_survives_the_shortest_window(self):
        self.assertIn("old overdue", self._titles("day"))
        self.assertNotIn("old done", self._titles("day"))

    def test_a_longer_window_brings_the_old_rows_back(self):
        self.assertIn("old done", self._titles("2months"))
        self.assertIn("undated old", self._titles("2months"))

    def test_all_time_filters_nothing(self):
        self.assertEqual(len(self._titles("all")), 5)

    def test_missing_or_unknown_window_is_all_time(self):
        c = TestClient()
        c.force_login(self.user)
        for params in ({"tab": "my"}, {"tab": "my", "window": "junk"}):
            self.assertEqual(
                len(c.get(reverse("clients:app_tasks"), params).json()["tasks"]), 5)

    def test_scorecard_honours_the_same_windows(self):
        c = TestClient()
        c.force_login(self.user)
        week = c.get(reverse("clients:app_task_scorecard"), {"period": "week"}).json()
        every = c.get(reverse("clients:app_task_scorecard"), {"period": "all"}).json()
        # The scorecard scores tasks *assigned* in the window — only one was.
        self.assertEqual(week["scorecard"][0]["total"], 1)
        self.assertEqual(every["scorecard"][0]["total"], 5)


class AppCrashReportTests(TestCase):
    """Self-hosted APK, no Play Console — a field crash used to produce no
    signal at all beyond 'the app closed'."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("crash_emp", password="x")
        Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)

    def _post(self, payload):
        import json as _json
        c = TestClient()
        c.force_login(self.user)
        return c.post(reverse("clients:app_crash"), data=_json.dumps(payload),
                      content_type="application/json")

    def test_crash_is_recorded_in_the_audit_log(self):
        from clients.models import AuditLog
        resp = self._post({
            "message": "java.lang.NullPointerException: boom",
            "stack": "at bo.kadlaginvestment.crm.Thing.run(Thing.kt:42)",
            "app_version": "4.23.0", "device": "Xiaomi Redmi Note 12", "android": 34,
        })
        self.assertEqual(resp.status_code, 200)
        row = AuditLog.objects.filter(action="app.crash").latest("id")
        self.assertEqual(row.actor, self.user)
        self.assertIn("NullPointerException", row.details["message"])
        self.assertIn("Thing.kt:42", row.details["stack"])
        self.assertEqual(row.details["app_version"], "4.23.0")
        self.assertIn("Redmi", row.summary)

    def test_oversized_stack_is_truncated_not_rejected(self):
        from clients.models import AuditLog
        resp = self._post({"message": "x", "stack": "y" * 20000})
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(AuditLog.objects.latest("id").details["stack"]), 6000)

    def test_garbage_body_is_rejected(self):
        c = TestClient()
        c.force_login(self.user)
        resp = c.post(reverse("clients:app_crash"), data="not json",
                      content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_requires_login(self):
        import json as _json
        resp = TestClient().post(reverse("clients:app_crash"), data=_json.dumps({}),
                                 content_type="application/json")
        self.assertEqual(resp.status_code, 302)

"""The single sidebar nav (base.html + context_processors.nav) shows the
right items per role. Tests the resolved flag set directly (reliable) plus a
render smoke test, so the dedupe can't silently regress.

Run: .venv/bin/python manage.py test clients.test.test_nav
"""
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client as TestClient, RequestFactory, TestCase
from django.urls import reverse

from clients.context_processors import nav as nav_ctx
from clients.models import Employee, ManagerAccessConfig


def _mk(username, role):
    user = User.objects.create_user(username=username, password="x")
    Employee.objects.create(user=user, role=role, salary=0, active=True)
    return user


class NavFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk("nav_admin", "admin")
        cls.manager = _mk("nav_manager", "manager")
        cls.employee = _mk("nav_employee", "employee")
        cls.rf = RequestFactory()

    def _flags(self, user):
        req = self.rf.get("/")
        req.user = user
        return nav_ctx(req)["nav"]

    def test_anonymous_gets_no_nav(self):
        req = self.rf.get("/")
        req.user = AnonymousUser()
        self.assertEqual(nav_ctx(req), {})

    def test_admin_flags(self):
        f = self._flags(self.admin)
        self.assertTrue(f["is_admin"])
        self.assertTrue(f["dashboard_admin"])
        self.assertTrue(f["show_mf"])
        self.assertTrue(f["show_settings"])
        self.assertTrue(f["show_bulk_reassign"])
        self.assertTrue(f["can_approve"])
        self.assertTrue(f["can_incentives"])
        self.assertTrue(f["can_analysis"])
        self.assertTrue(f["can_leads"])
        self.assertTrue(f["show_tasks_categories"])
        self.assertFalse(f["show_my_clients"])       # admin has no "My Clients"

    def test_employee_flags(self):
        f = self._flags(self.employee)
        self.assertTrue(f["is_employee"])
        self.assertFalse(f["dashboard_admin"])
        self.assertFalse(f["show_mf"])
        self.assertFalse(f["show_settings"])
        self.assertFalse(f["show_bulk_reassign"])
        self.assertFalse(f["can_approve"])
        self.assertFalse(f["can_incentives"])
        self.assertTrue(f["can_analysis"])           # employees keep analysis
        self.assertTrue(f["can_leads"])              # employees keep leads
        self.assertTrue(f["show_my_clients"])

    def test_manager_flags_follow_config(self):
        cfg = ManagerAccessConfig.current()
        for field in ("allow_approve_sales", "allow_manage_incentives",
                      "allow_lead_management", "allow_business_tracking",
                      "allow_employee_performance", "allow_client_analysis"):
            setattr(cfg, field, False)
        cfg.save()
        f = self._flags(self.manager)
        self.assertFalse(f["can_approve"])
        self.assertFalse(f["can_incentives"])
        self.assertFalse(f["can_leads"])
        self.assertFalse(f["show_reports_menu"])
        self.assertFalse(f["show_mf"])               # MF stays admin-only
        self.assertFalse(f["show_settings"])

        cfg.allow_approve_sales = True
        cfg.allow_manage_incentives = True
        cfg.allow_lead_management = True
        cfg.allow_employee_performance = True
        cfg.save()
        f = self._flags(self.manager)
        self.assertTrue(f["can_approve"])
        self.assertTrue(f["can_incentives"])
        self.assertTrue(f["can_leads"])
        self.assertTrue(f["show_reports_menu"])


class NavRenderSmokeTests(TestCase):
    """base.html renders without error for every role."""

    @classmethod
    def setUpTestData(cls):
        cls.users = {r: _mk(f"navr_{r}", r) for r in ("admin", "manager", "employee")}

    def test_each_role_renders_ok(self):
        for role, user in self.users.items():
            c = TestClient()
            c.force_login(user)
            resp = c.get(reverse("clients:all_clients"))
            self.assertEqual(resp.status_code, 200, f"{role} failed to render")
            # nav landmark present
            self.assertContains(resp, 'id="navMenu"')

"""Role × URL smoke tests. The minimum safety net for the most important
auth boundary in the app. If any of these regress, a role can suddenly see
data it shouldn't.

Run: .venv/bin/python manage.py test clients.test.test_role_permissions -v 2
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from clients.models import Employee


class _RoleSetup(TestCase):
    """Creates one user per role + Employee record. Subclasses get self.{admin,manager,employee}_client."""

    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for role in ("admin", "manager", "employee"):
            user = User.objects.create_user(
                username=f"test_{role}",
                password="testpass123",
                email=f"{role}@test.local",
                first_name=role.capitalize(),
            )
            Employee.objects.create(user=user, role=role, salary=0, active=True)
            cls.users[role] = user

    def setUp(self):
        # Each test method gets fresh logged-in clients.
        self.clients = {}
        for role, user in self.users.items():
            c = Client()
            c.force_login(user)
            self.clients[role] = c

    def assertStatus(self, role, url_name, expected, args=None, msg=""):
        url = reverse(url_name, args=args or [])
        resp = self.clients[role].get(url)
        self.assertEqual(
            resp.status_code,
            expected,
            f"{role} GET {url} expected {expected}, got {resp.status_code}. {msg}",
        )


class AdminAccessTests(_RoleSetup):
    """Admin should reach every admin-only and shared page."""

    def test_admin_dashboard(self):
        self.assertStatus("admin", "clients:admin_dashboard", 200)

    def test_admin_can_access_all_clients(self):
        self.assertStatus("admin", "clients:all_clients", 200)

    def test_admin_can_access_all_sales(self):
        self.assertStatus("admin", "clients:all_sales", 200)

    def test_admin_can_access_approve_sales(self):
        self.assertStatus("admin", "clients:approve_sales", 200)

    def test_admin_can_access_past_performance(self):
        self.assertStatus("admin", "clients:admin_past_performance", 200)

    def test_admin_can_access_team(self):
        self.assertStatus("admin", "clients:team_list", 200)

    def test_admin_can_access_firm_settings(self):
        self.assertStatus("admin", "clients:firm_settings", 200)

    def test_admin_can_access_product_management(self):
        self.assertStatus("admin", "clients:product_management", 200)


class EmployeeAccessTests(_RoleSetup):
    """Employee should see their own dashboard + shared pages, NOT admin-only ones."""

    def test_employee_dashboard(self):
        self.assertStatus("employee", "clients:employee_dashboard", 200)

    def test_employee_can_view_all_clients(self):
        # Opened up to all roles in recent change — verify it still works.
        self.assertStatus("employee", "clients:all_clients", 200)

    def test_employee_can_view_my_clients(self):
        self.assertStatus("employee", "clients:my_clients", 200)

    def test_employee_BLOCKED_from_past_performance(self):
        # Reports view returns 403 for non-admin/manager.
        self.assertStatus("employee", "clients:admin_past_performance", 403)

    def test_employee_BLOCKED_from_firm_settings(self):
        # firm_settings should be admin-only.
        resp = self.clients["employee"].get(reverse("clients:firm_settings"))
        # Could be 403 or redirect to a permission-denied page; both are acceptable defenses.
        self.assertIn(resp.status_code, (302, 403), f"got {resp.status_code}")

    def test_employee_BLOCKED_from_team_list(self):
        resp = self.clients["employee"].get(reverse("clients:team_list"))
        self.assertIn(resp.status_code, (302, 403))


class ManagerAccessTests(_RoleSetup):
    """Manager capability is gated by ManagerAccessConfig (singleton, id=1).
    Defaults: allow_employee_performance=True, allow_view_all_sales=True; most others False."""

    def test_manager_dashboard(self):
        self.assertStatus("manager", "clients:employee_dashboard", 200)

    def test_manager_can_view_all_clients(self):
        self.assertStatus("manager", "clients:all_clients", 200)

    def test_manager_can_view_my_clients(self):
        self.assertStatus("manager", "clients:my_clients", 200)

    def test_manager_past_performance_with_default_config(self):
        # Default: allow_employee_performance=True → manager CAN view.
        self.assertStatus("manager", "clients:admin_past_performance", 200)

    def test_manager_past_performance_when_flag_disabled(self):
        # Explicitly turn off the capability → expect 403.
        from clients.models import ManagerAccessConfig
        cfg = ManagerAccessConfig.current()
        cfg.allow_employee_performance = False
        cfg.save()
        self.assertStatus("manager", "clients:admin_past_performance", 403)

    def test_manager_approve_sales_default_blocked(self):
        # Default: allow_approve_sales=False → manager should NOT see approve_sales.
        resp = self.clients["manager"].get(reverse("clients:approve_sales"))
        # Could be 403 or redirect; both indicate denial.
        self.assertIn(resp.status_code, (302, 403))


class UnauthenticatedTests(TestCase):
    """Anonymous users should be bounced to login on every protected URL."""

    def test_unauth_redirected(self):
        for url_name in [
            "clients:admin_dashboard",
            "clients:employee_dashboard",
            "clients:all_clients",
            "clients:my_clients",
            "clients:all_sales",
        ]:
            url = reverse(url_name)
            resp = Client().get(url)
            self.assertIn(
                resp.status_code, (302, 403),
                f"Anonymous GET {url} should redirect to login or 403; got {resp.status_code}",
            )


class ClientProfileAccessTests(_RoleSetup):
    """The new client_profile view was opened up to all authenticated users —
    confirm that's still true (regression test for the recent change)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from clients.models import Client as ClientModel
        cls.client_obj = ClientModel.objects.create(
            id=99001, name="Test Client", status="Unmapped",
        )

    def test_all_roles_can_view_client_profile(self):
        for role in ("admin", "manager", "employee"):
            self.assertStatus(role, "clients:client_profile", 200, args=[self.client_obj.id])

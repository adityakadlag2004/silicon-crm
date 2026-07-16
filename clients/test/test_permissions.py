"""Unit tests for clients/permissions.py — the single source of truth for
authorization — plus regression checks for gates that moved onto it.

Run: .venv/bin/python manage.py test clients.test.test_permissions
"""
import json

from django.contrib.auth.models import AnonymousUser, User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients import permissions
from clients.models import Employee, ManagerAccessConfig


def _mk(username, role=None, superuser=False):
    if superuser:
        user = User.objects.create_superuser(username=username, password="x")
    else:
        user = User.objects.create_user(username=username, password="x")
    if role:
        Employee.objects.create(user=user, role=role, salary=0, active=True)
    return user


class PermissionHelperTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = _mk("p_admin", "admin")
        cls.manager = _mk("p_manager", "manager")
        cls.employee = _mk("p_employee", "employee")
        cls.superuser = _mk("p_super", superuser=True)

    def test_is_admin(self):
        self.assertTrue(permissions.is_admin(self.admin))
        self.assertTrue(permissions.is_admin(self.superuser))
        self.assertFalse(permissions.is_admin(self.manager))
        self.assertFalse(permissions.is_admin(self.employee))
        self.assertFalse(permissions.is_admin(AnonymousUser()))

    def test_is_admin_or_manager(self):
        self.assertTrue(permissions.is_admin_or_manager(self.admin))
        self.assertTrue(permissions.is_admin_or_manager(self.manager))
        self.assertFalse(permissions.is_admin_or_manager(self.employee))

    def test_role_choices_match_legacy_strings(self):
        self.assertEqual(Employee.Role.ADMIN, "admin")
        self.assertEqual(Employee.Role.MANAGER, "manager")
        self.assertEqual(Employee.Role.EMPLOYEE, "employee")

    def test_can_admin_always(self):
        self.assertTrue(permissions.can(self.admin, "approve_sales"))
        self.assertTrue(permissions.can(self.superuser, "manage_incentives"))

    def test_can_employee_never(self):
        cfg = ManagerAccessConfig.current()
        cfg.allow_approve_sales = True
        cfg.save()
        self.assertFalse(permissions.can(self.employee, "approve_sales"))

    def test_can_manager_follows_flag(self):
        cfg = ManagerAccessConfig.current()
        cfg.allow_approve_sales = False
        cfg.save()
        self.assertFalse(permissions.can(self.manager, "approve_sales"))
        cfg.allow_approve_sales = True
        cfg.save()
        self.assertTrue(permissions.can(self.manager, "approve_sales"))

    def test_can_unknown_flag_denied(self):
        self.assertFalse(permissions.can(self.manager, "nonexistent_flag"))
        self.assertTrue(permissions.can(self.admin, "nonexistent_flag"))  # admins bypass flags


class GateRegressionTests(TestCase):
    """Manager-flag gates that moved onto permissions.can() keep behaving."""

    @classmethod
    def setUpTestData(cls):
        cls.manager = _mk("g_manager", "manager")
        cls.employee = _mk("g_employee", "employee")

    def _http(self, user):
        c = TestClient()
        c.force_login(user)
        return c

    def test_manager_with_incentives_flag_reaches_incentive_rules(self):
        cfg = ManagerAccessConfig.current()
        cfg.allow_manage_incentives = True
        cfg.save()
        resp = self._http(self.manager).get(reverse("clients:manage_incentive_rules"))
        self.assertEqual(resp.status_code, 200)

    def test_manager_without_incentives_flag_redirected(self):
        cfg = ManagerAccessConfig.current()
        cfg.allow_manage_incentives = False
        cfg.save()
        resp = self._http(self.manager).get(reverse("clients:manage_incentive_rules"))
        self.assertEqual(resp.status_code, 302)

    def test_employee_blocked_from_campaign_write(self):
        resp = self._http(self.employee).post(
            reverse("clients:add_campaign"),
            data=json.dumps({"name": "X"}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_employee_blocked_from_audit_log(self):
        resp = self._http(self.employee).get(reverse("clients:audit_log"))
        self.assertEqual(resp.status_code, 403)

    def test_employee_blocked_from_mf_dashboard(self):
        resp = self._http(self.employee).get(reverse("clients:mf_dashboard"))
        self.assertEqual(resp.status_code, 403)

"""Smoke check: the two reshaped screens render with the shell markup."""
from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from clients.models import Client, Employee


class ShellRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("shelladmin", password="pw")
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        cls.c = Client.objects.create(id=9001, name="Parekh Family", phone="9812345678",
                                      lumsum_investment=250000, sip_status=True)

    def _get(self, url):
        tc = TC(); tc.force_login(self.u)
        r = tc.get(url)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_list_has_shell(self):
        html = self._get(reverse("clients:all_clients"))
        for frag in ('ki-crumb', 'ki-kpis', 'ki-kpi', 'ki-mono', 'ki-dtable', 'All Clients'):
            self.assertIn(frag, html, frag)

    def test_detail_has_shell(self):
        html = self._get(reverse("clients:client_profile", args=[9001]))
        for frag in ('ki-crumb', 'ki-rec', 'ki-mono', 'ki-rec-summary', 'ki-tag', 'PF'):
            self.assertIn(frag, html, frag)
        self.assertIn("2,50,000", html)   # inr filter reached the tile


class ShellAcrossModulesTests(TestCase):
    """The shell partials render on leads and tasks too, not just clients."""

    @classmethod
    def setUpTestData(cls):
        from clients.models import Lead, Task
        cls.u = User.objects.create_user("shell2", password="pw")
        cls.emp = Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        cls.lead = Lead.objects.create(customer_name="Anand Jain", assigned_to=cls.emp)
        cls.task = Task.objects.create(title="Collect KYC", created_by=cls.u, assigned_to=cls.emp)

    def _get(self, url):
        tc = TC(); tc.force_login(self.u)
        r = tc.get(url)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_lead_list_and_detail(self):
        html = self._get(reverse("clients:lead_management"))
        self.assertIn("ki-kpis", html)
        self.assertIn("ki-mono", html)
        detail = self._get(reverse("clients:lead_detail", args=[self.lead.id]))
        self.assertIn("ki-rec", detail)
        self.assertIn("AJ", detail)          # monogram initials

    def test_task_detail(self):
        html = self._get(reverse("clients:task_detail", args=[self.task.pk]))
        self.assertIn("ki-crumb", html)
        self.assertIn("ki-rec-top", html)
        self.assertIn("CK", html)


class NavSidebarTests(TestCase):
    """The nav still renders every module for the roles that may see them."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("navadmin", password="pw")
        Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.emp = User.objects.create_user("navemp", password="pw")
        Employee.objects.create(user=cls.emp, role="employee", salary=0, active=True)

    def _home(self, user):
        tc = TC(); tc.force_login(user)
        r = tc.get(reverse("clients:all_clients"))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_admin_sees_monogrammed_modules(self):
        html = self._home(self.admin)
        for mono in ("Db", "Cl", "Sa", "In", "MF", "Ta", "Re", "Se"):
            self.assertIn(f'kn-mono" style="--c:', html)
            self.assertIn(f">{mono}</span>", html)
        # Links has no group of its own — it is a section inside Tasks.
        self.assertIn(reverse("clients:links_dashboard"), html)

    def test_employee_nav_still_renders(self):
        # Permission-gated groups may be absent; the shared ones must not be.
        html = self._home(self.emp)
        self.assertIn("kn-nav", html)
        self.assertIn(">Cl</span>", html)
        self.assertIn(">Ta</span>", html)

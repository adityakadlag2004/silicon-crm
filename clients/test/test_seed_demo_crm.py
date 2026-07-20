"""The demo seeder must be reversible and must never touch real records."""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from clients.models import (
    Client, Employee, Family, InsuranceClaim, InsurancePolicy, Meeting,
)


@override_settings(DEBUG=True)
class SeedDemoCrmTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ("nirbhay", "harshda"):
            u = User.objects.create_user(name, password="pw", first_name=name.title())
            Employee.objects.create(user=u, role="employee", salary=0, active=True)
        # A real client and household that must survive both seed and undo.
        cls.real_client = Client.objects.create(id=42, name="Real Client",
                                                lumsum_investment=100000)
        cls.real_family = Family.objects.create(name="Real Family", code="A001")
        cls.real_policy = InsurancePolicy.objects.create(
            client=cls.real_client, policy_number="REAL123", insurer="LIC")

    def _seed(self, *args):
        call_command("seed_demo_crm", *args, stdout=StringIO())

    def test_seeding_creates_all_four_modules(self):
        self._seed()
        self.assertTrue(Family.objects.filter(code__startswith="DEMO-").exists())
        self.assertTrue(InsurancePolicy.objects.filter(policy_number__startswith="DEMO-").exists())
        self.assertTrue(InsuranceClaim.objects.exists())
        self.assertTrue(Meeting.objects.exists())
        # Households get a head and members, so combined AUM is non-zero.
        fam = Family.objects.filter(code__startswith="DEMO-").first()
        self.assertIsNotNone(fam.head)
        self.assertGreater(fam.aum, 0)

    def test_seeding_twice_does_not_duplicate(self):
        self._seed()
        counts = (Family.objects.count(), InsurancePolicy.objects.count(),
                  Meeting.objects.count(), Client.objects.count())
        self._seed()
        self.assertEqual(
            (Family.objects.count(), InsurancePolicy.objects.count(),
             Meeting.objects.count(), Client.objects.count()),
            counts)

    def test_undo_removes_demo_data_and_spares_real_records(self):
        self._seed()
        self._seed("--undo")
        self.assertFalse(Family.objects.filter(code__startswith="DEMO-").exists())
        self.assertFalse(InsurancePolicy.objects.filter(policy_number__startswith="DEMO-").exists())
        self.assertFalse(Client.objects.filter(id__gte=990_000).exists())
        # The real records are untouched.
        self.assertTrue(Client.objects.filter(id=42).exists())
        self.assertTrue(Family.objects.filter(code="A001").exists())
        self.assertTrue(InsurancePolicy.objects.filter(policy_number="REAL123").exists())

    def test_seed_populates_the_kpi_edge_cases(self):
        self._seed()
        # The dashboard tiles are only interesting if these states exist.
        self.assertTrue(InsurancePolicy.objects.filter(
            status=InsurancePolicy.STATUS_LAPSED).exists())
        self.assertTrue(any(p.is_expiring_soon for p in InsurancePolicy.objects.all()))
        self.assertTrue(InsuranceClaim.objects.filter(
            status=InsuranceClaim.STATUS_SETTLED).exists())
        self.assertTrue(InsuranceClaim.objects.filter(
            status__in=InsuranceClaim.OPEN_STATUSES).exists())
        self.assertTrue(any(m.is_overdue for m in Meeting.objects.all()))


class SeedDemoCrmGuardTests(TestCase):
    @override_settings(DEBUG=False)
    def test_refuses_to_run_without_force_when_debug_is_off(self):
        out = StringIO()
        call_command("seed_demo_crm", stdout=out)
        self.assertIn("--force", out.getvalue())
        self.assertFalse(Family.objects.filter(code__startswith="DEMO-").exists())

"""Insurance Tracker and Claim Tracker: derived fields + screens."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Client, Employee, InsuranceClaim, InsurancePolicy,
)


class InsuranceModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_rec = Client.objects.create(id=8001, name="Vishal Bose")

    def _policy(self, **kw):
        defaults = dict(client=self.client_rec, policy_number="INS7612399",
                        insurer="ICICI Lombard", sum_insured=718968, premium_amount=76209)
        defaults.update(kw)
        return InsurancePolicy.objects.create(**defaults)

    def test_expiring_soon_only_inside_the_30_day_window(self):
        today = timezone.localdate()
        cases = [
            (today + timedelta(days=1), True),
            (today + timedelta(days=30), True),    # boundary: still "soon"
            (today + timedelta(days=31), False),
            (today - timedelta(days=1), False),    # already expired, not "soon"
            (None, False),
        ]
        for end, expected in cases:
            p = self._policy(end_date=end)
            self.assertIs(p.is_expiring_soon, expected, f"end_date={end}")

    def test_lapsed_policy_is_never_expiring_soon(self):
        p = self._policy(end_date=timezone.localdate() + timedelta(days=5),
                         status=InsurancePolicy.STATUS_LAPSED)
        self.assertFalse(p.is_expiring_soon)

    def test_claim_shortfall(self):
        p = self._policy()
        c = InsuranceClaim.objects.create(policy=p, claimed_amount=245500,
                                          settled_amount=218750)
        self.assertEqual(c.shortfall, 26750)


class InsuranceScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("insadmin", password="pw")
        cls.emp = Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        cls.c = Client.objects.create(id=8100, name="Vishal Bose")
        cls.policy = InsurancePolicy.objects.create(
            client=cls.c, policy_number="INS7612399", insurer="ICICI Lombard",
            sum_insured=718968, premium_amount=76209,
            end_date=timezone.localdate() + timedelta(days=10))
        cls.claim = InsuranceClaim.objects.create(
            policy=cls.policy, claim_type="Hospitalisation Claim",
            claimed_amount=245500, settled_amount=218750,
            status=InsuranceClaim.STATUS_SETTLED)

    def _get(self, url, **params):
        tc = TC(); tc.force_login(self.u)
        r = tc.get(url, params)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_policy_screens(self):
        lst = self._get(reverse("clients:policy_list"))
        self.assertIn("Vishal Bose", lst)
        self.assertIn("7,18,968", lst)
        self.assertIn("ki-kpis", lst)
        detail = self._get(reverse("clients:policy_detail", args=[self.policy.id]))
        self.assertIn("ICICI Lombard", detail)
        self.assertIn("Hospitalisation Claim", detail)

    def test_expiring_filter_uses_the_date_window(self):
        html = self._get(reverse("clients:policy_list"), expiring="1")
        self.assertIn("INS7", html)          # masked number still identifiable
        far = InsurancePolicy.objects.create(
            client=self.c, policy_number="FARAWAY9999", insurer="HDFC Ergo",
            end_date=timezone.localdate() + timedelta(days=200))
        html2 = self._get(reverse("clients:policy_list"), expiring="1")
        self.assertNotIn("FARA", html2)

    def test_claim_screens_show_shortfall(self):
        lst = self._get(reverse("clients:claim_list"))
        self.assertIn("Hospitalisation Claim", lst)
        detail = self._get(reverse("clients:claim_detail", args=[self.claim.id]))
        self.assertIn("26,750", detail)      # 2,45,500 - 2,18,750

    def test_policy_number_is_masked_in_the_list(self):
        html = self._get(reverse("clients:policy_list"))
        self.assertNotIn("INS7612399", html)

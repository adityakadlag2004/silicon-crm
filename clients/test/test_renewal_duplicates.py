"""One renewal per policy per cycle, guarded on both write paths.

The web view and the app API never share a save path, so each has to check —
a guard on either one alone leaves the other door open.
"""
import datetime
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clients.models import Client, Employee, InsurancePolicy, Product, Renewal
from clients.services import insurance_sync


class DuplicateRenewalGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.emp = Employee.objects.create(
            user=User.objects.create_user("dup_emp", password="x"),
            role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="DUP Client", mapped_to=cls.emp)
        cls.policy = InsurancePolicy.objects.create(
            client=cls.customer, policy_number="DUP123", insurer="Star",
            insurance_type=InsurancePolicy.TYPE_HEALTH,
            start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 4, 1))
        cls.existing = Renewal.objects.create(
            client=cls.customer, policy=cls.policy, product_ref=cls.health,
            product_type=Renewal.PRODUCT_TYPE_HEALTH,
            renewal_date=datetime.date(2026, 4, 1),
            frequency=Renewal.FREQUENCY_YEARLY,
            premium_amount=Decimal("22000"), employee=cls.emp)

    def _dup(self, when, **kw):
        return insurance_sync.duplicate_renewal(
            client=self.customer, renewal_date=when,
            frequency=Renewal.FREQUENCY_YEARLY, **kw)

    def test_same_policy_inside_the_cycle_is_a_duplicate(self):
        self.assertEqual(self._dup(datetime.date(2026, 5, 10), policy=self.policy),
                         self.existing)

    def test_next_year_is_not_a_duplicate(self):
        self.assertIsNone(self._dup(datetime.date(2027, 4, 1), policy=self.policy))

    def test_policy_number_alone_identifies_the_clash(self):
        # no policy ticked — the typed number is what catches it
        self.assertEqual(self._dup(datetime.date(2026, 5, 10), policy_number="dup123 "),
                         self.existing)

    def test_a_different_policy_is_not_a_duplicate(self):
        other = InsurancePolicy.objects.create(
            client=self.customer, policy_number="OTHER9",
            insurance_type=InsurancePolicy.TYPE_LIFE)
        self.assertIsNone(self._dup(datetime.date(2026, 5, 10), policy=other))

    def test_shorter_frequency_narrows_the_window(self):
        self.existing.frequency = Renewal.FREQUENCY_MONTHLY
        self.existing.save(update_fields=["frequency"])
        # 40 days apart is two monthly collections, not a double entry
        self.assertIsNone(insurance_sync.duplicate_renewal(
            client=self.customer, renewal_date=datetime.date(2026, 5, 11),
            frequency=Renewal.FREQUENCY_MONTHLY, policy=self.policy))

    def test_message_names_the_policy_and_the_date(self):
        msg = insurance_sync.duplicate_message(self.existing)
        self.assertIn("DUP123", msg)
        self.assertIn("01 Apr 2026", msg)


class DuplicateRenewalWritePathTests(TestCase):
    """Both doors refuse, and both allow a confirmed override."""

    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.health.domain = Product.DOMAIN_BOTH
        cls.health.save()
        cls.user = User.objects.create_user("dup_web", password="x")
        cls.emp = Employee.objects.create(
            user=cls.user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="DUP2 Client", mapped_to=cls.emp)
        cls.policy = InsurancePolicy.objects.create(
            client=cls.customer, policy_number="WEB123",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        Renewal.objects.create(
            client=cls.customer, policy=cls.policy, product_ref=cls.health,
            product_type=Renewal.PRODUCT_TYPE_HEALTH,
            renewal_date=datetime.date(2026, 4, 1),
            frequency=Renewal.FREQUENCY_YEARLY,
            premium_amount=Decimal("15000"), employee=cls.emp)

    def _payload(self, **extra):
        data = {
            "client": self.customer.id, "product_ref": self.health.id,
            "renewal_date": "2026-05-10", "frequency": Renewal.FREQUENCY_YEARLY,
            "premium_amount": "15000", "premium_collected_on": "2026-05-10",
            "employee": self.emp.id, "policy": self.policy.id,
        }
        data.update(extra)
        return data

    def test_web_post_is_refused_and_nothing_is_written(self):
        self.client.force_login(self.user)
        before = Renewal.objects.count()
        resp = self.client.post(reverse("clients:add_renewal"), self._payload())
        self.assertEqual(resp.status_code, 200)          # re-rendered, not redirected
        self.assertContains(resp, "Already added")
        self.assertContains(resp, "add it anyway")       # the override is offered
        self.assertEqual(Renewal.objects.count(), before)

    def test_web_post_goes_through_once_confirmed(self):
        self.client.force_login(self.user)
        before = Renewal.objects.count()
        resp = self.client.post(reverse("clients:add_renewal"),
                                self._payload(confirm_duplicate="1"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Renewal.objects.count(), before + 1)

    def test_app_post_is_refused_with_409(self):
        self.client.force_login(self.user)
        before = Renewal.objects.count()
        resp = self.client.post(
            reverse("clients:app_renewal_create"),
            data=json.dumps({
                "client_id": self.customer.id, "product_id": self.health.id,
                "renewal_date": "2026-05-10", "frequency": Renewal.FREQUENCY_YEARLY,
                "premium_amount": "15000", "policy_id": self.policy.id,
            }), content_type="application/json")
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertTrue(body["duplicate"])
        self.assertIn("Already added", body["error"])
        self.assertEqual(Renewal.objects.count(), before)

    def test_app_post_goes_through_once_confirmed(self):
        self.client.force_login(self.user)
        before = Renewal.objects.count()
        resp = self.client.post(
            reverse("clients:app_renewal_create"),
            data=json.dumps({
                "client_id": self.customer.id, "product_id": self.health.id,
                "renewal_date": "2026-05-10", "frequency": Renewal.FREQUENCY_YEARLY,
                "premium_amount": "15000", "policy_id": self.policy.id,
                "confirm_duplicate": True,
            }), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(Renewal.objects.count(), before + 1)

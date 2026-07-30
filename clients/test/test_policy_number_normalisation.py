"""A policy number is a matching key, so it normalises wherever it enters.

The Android screens used to upper-case it while the user typed, which reset the
cursor and made a mid-number typo unfixable. Removing that only stays safe if
the server normalises on every path — including renewal linking, which stripped
but never upper-cased.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from clients.models import (Client, Employee, InsurancePolicy, Product,
                            Renewal, Sale)
from clients.services import insurance_sync


class PolicyNumberNormalisationTests(TestCase):
    def setUp(self):
        self.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        self.user = User.objects.create_user("emp", password="x")
        self.emp = Employee.objects.create(user=self.user, role="employee")
        self.client_rec = Client.objects.create(name="A Client")
        self.today = date.today()

    def test_a_policy_upper_cases_and_strips_on_save(self):
        p = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="  ins123 ", insurer="ICICI",
            start_date=self.today, end_date=self.today + timedelta(days=365))
        self.assertEqual(p.policy_number, "INS123")
        p.refresh_from_db()
        self.assertEqual(p.policy_number, "INS123")

    def test_the_same_number_typed_two_ways_is_one_policy(self):
        for raw in ("ins123", "  INS123  "):
            InsurancePolicy.objects.create(
                client=self.client_rec, policy_number=raw, insurer="ICICI",
                start_date=self.today, end_date=self.today + timedelta(days=365))
        self.assertEqual(
            InsurancePolicy.objects.filter(policy_number="INS123").count(), 2,
            msg="both rows should normalise to the same key")
        self.assertFalse(
            InsurancePolicy.objects.filter(policy_number="ins123").exists())

    def test_renewal_linking_normalises_a_lowercase_number(self):
        """The gap the app's client-side uppercasing was hiding."""
        renewal = Renewal.objects.create(
            client=self.client_rec, employee=self.emp, product_type="health_insurance",
            product_name="Health Insurance", product_ref=self.health,
            premium_amount=Decimal("20000"), renewal_date=self.today)
        # The view resolves its "New policy" choice to no pk before calling.
        insurance_sync.link_renewal_to_policy(
            renewal, selected_policy_id=None, new_policy_number=" hlt-77 ")
        renewal.refresh_from_db()
        self.assertIsNotNone(renewal.policy)
        self.assertEqual(renewal.policy.policy_number, "HLT-77")

    def test_a_sale_synced_to_the_tracker_keeps_one_key(self):
        sale = Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("20000"), date=self.today,
            policy_date=self.today, policy_number=" hs-9 ",
            status=Sale.STATUS_APPROVED, policy_type="fresh")
        self.assertEqual(sale.policy_number, "HS-9")
        insurance_sync.sync_policy_from_sale(sale)
        policy = InsurancePolicy.objects.get(source_sale=sale)
        self.assertEqual(policy.policy_number, "HS-9")

    def test_unsyncing_still_recognises_an_untouched_policy(self):
        # insurance_sync compares the policy number against the sale's to decide
        # whether a human has edited it. Normalising both must not break that.
        sale = Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("20000"), date=self.today,
            policy_date=self.today, policy_number="hs-10",
            status=Sale.STATUS_APPROVED, policy_type="fresh")
        insurance_sync.sync_policy_from_sale(sale)
        self.assertTrue(InsurancePolicy.objects.filter(source_sale=sale).exists())
        insurance_sync.unsync_policy_for_sale(sale)
        self.assertFalse(InsurancePolicy.objects.filter(source_sale=sale).exists())

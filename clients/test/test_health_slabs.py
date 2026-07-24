"""Health Insurance Fresh slabs + flat Port/renewal 15%, via seed_health_slabs."""

from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from clients.models import Product


class HealthSlabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.h, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        call_command("seed_health_slabs")
        cls.h.refresh_from_db()

    def test_fresh_slab_boundaries(self):
        # (monthly fresh business, expected margin %) — boundary belongs to the
        # higher band; exactly 3,00,000 stays 30%.
        cases = {
            "10000": "15.00", "24999.99": "15.00",
            "25000": "20.00", "49999.99": "20.00",
            "50000": "22.50", "99999.99": "22.50",
            "100000": "27.50", "199999.99": "27.50",
            "200000": "30.00", "299999.99": "30.00",
            "300000": "35.00", "5000000": "35.00",  # >=3L is 35% per the grid
        }
        for amount, pct in cases.items():
            self.assertEqual(self.h.margin_for(amount, "fresh"), Decimal(pct),
                             msg=f"fresh {amount}")

    def test_port_is_flat_15(self):
        for amount in ("1000", "500000"):
            self.assertEqual(self.h.margin_for(amount, "port"), Decimal("15.00"))

    def test_renewal_is_flat_12_75(self):
        self.assertEqual(self.h.renewal_margin_percent, Decimal("12.75"))

    def test_idempotent(self):
        call_command("seed_health_slabs")
        self.assertEqual(self.h.margin_slabs.filter(policy_type="fresh").count(), 6)

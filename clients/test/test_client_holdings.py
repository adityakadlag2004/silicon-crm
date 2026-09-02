"""What a client holds is derived from their approved sales.

The Portfolio cards on the client profile read these columns, so anything the
recompute forgets is invisible on the profile however many sales were booked.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from clients.models import Client, Employee, Product, Sale


def _sale(client, emp, product, amount, cover=None, status=Sale.STATUS_APPROVED):
    return Sale.objects.create(
        client=client, employee=emp, product_ref=product, product=product.name,
        amount=Decimal(amount), cover_amount=(Decimal(cover) if cover else None),
        status=status, date=timezone.localdate(),
        policy_number=("P%s" % product.code if product.code.endswith("INS") else ""),
        policy_date=timezone.localdate() if product.code.endswith("INS") else None,
    )


class ClientHoldingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            user=User.objects.create_user("hold_emp", password="x"),
            role="employee", salary=0, active=True)
        cls.lumsum, _ = Product.objects.get_or_create(code="LUMSUM", defaults={"name": "Lumsum"})
        cls.sip, _ = Product.objects.get_or_create(code="SIP", defaults={"name": "SIP"})
        cls.life, _ = Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance"})
        cls.health, _ = Product.objects.get_or_create(code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.term = Product.objects.create(code="TERM_PLAN", name="Term Plan", parent=cls.life)

    def test_lumpsum_sales_reach_the_profile(self):
        """It was never recomputed at all — only ever typed by hand — so every
        client with a lumpsum sale read as Rs 0."""
        c = Client.objects.create(name="Lump Client")
        _sale(c, self.emp, self.lumsum, "500000")
        c.refresh_from_db()
        self.assertEqual(c.lumsum_investment, Decimal("500000"))

    def test_two_lumpsum_sales_add_up(self):
        c = Client.objects.create(name="Lump Two")
        _sale(c, self.emp, self.lumsum, "500000")
        _sale(c, self.emp, self.lumsum, "250000")
        c.refresh_from_db()
        self.assertEqual(c.lumsum_investment, Decimal("750000"))

    def test_a_pending_sale_does_not_count(self):
        c = Client.objects.create(name="Lump Pending")
        _sale(c, self.emp, self.lumsum, "500000", status=Sale.STATUS_PENDING)
        c.refresh_from_db()
        self.assertEqual(c.lumsum_investment, 0)

    def test_sub_product_sale_rolls_up_into_its_parent_line(self):
        """A sale names the exact plan sold; matching on the parent code alone
        counted none of them, so a crore of cover read as no life insurance."""
        c = Client.objects.create(name="Sub Client")
        _sale(c, self.emp, self.term, "100000", cover="10000000")
        c.refresh_from_db()
        self.assertEqual(c.life_cover, Decimal("10000000"))
        self.assertTrue(c.life_status)

    def test_parent_and_sub_product_sales_combine(self):
        c = Client.objects.create(name="Both Client")
        _sale(c, self.emp, self.life, "50000", cover="2000000")
        _sale(c, self.emp, self.term, "100000", cover="10000000")
        c.refresh_from_db()
        self.assertEqual(c.life_cover, Decimal("12000000"))

    def test_insurance_with_blank_cover_still_reads_as_held(self):
        """Cover can be unknown; the policy is not. Keying the flag off cover
        alone told those clients they had no policy at all."""
        c = Client.objects.create(name="No Cover Client")
        _sale(c, self.emp, self.health, "18000", cover=None)
        c.refresh_from_db()
        self.assertEqual(c.health_cover, 0)
        self.assertTrue(c.health_status)

    def test_a_client_with_no_insurance_sale_stays_false(self):
        c = Client.objects.create(name="Nothing Client")
        _sale(c, self.emp, self.lumsum, "500000")
        c.refresh_from_db()
        self.assertFalse(c.health_status)
        self.assertFalse(c.life_status)

    def test_sip_amount_still_comes_from_sales(self):
        c = Client.objects.create(name="Sip Client")
        _sale(c, self.emp, self.sip, "8000")
        c.refresh_from_db()
        self.assertEqual(c.sip_amount, Decimal("8000"))
        self.assertTrue(c.sip_status)


class RecomputeCommandTests(TestCase):
    """The signal only fires on a sale save, so old rows need a sweep."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            user=User.objects.create_user("rc_emp", password="x"),
            role="employee", salary=0, active=True)
        cls.lumsum, _ = Product.objects.get_or_create(code="LUMSUM", defaults={"name": "Lumsum"})

    def _stale_client(self):
        c = Client.objects.create(name="Stale Client")
        _sale(c, self.emp, self.lumsum, "300000")
        # simulate a row written before the recompute knew about lumpsum
        Client.objects.filter(pk=c.pk).update(lumsum_investment=0)
        return c

    def test_dry_run_reports_but_writes_nothing(self):
        c = self._stale_client()
        call_command("recompute_client_holdings")
        c.refresh_from_db()
        self.assertEqual(c.lumsum_investment, 0)

    def test_apply_writes_the_corrected_value(self):
        c = self._stale_client()
        call_command("recompute_client_holdings", "--apply")
        c.refresh_from_db()
        self.assertEqual(c.lumsum_investment, Decimal("300000"))

    def test_second_run_is_a_no_op(self):
        self._stale_client()
        call_command("recompute_client_holdings", "--apply")
        from io import StringIO
        out = StringIO()
        call_command("recompute_client_holdings", "--apply", stdout=out)
        self.assertIn("0 client(s) updated", out.getvalue())

"""Renewal date logic + reminders: single-year policies renew yearly (remind at
30/15/5 days to the mapped employee); multiyear policies stay silent until their
paid term ends."""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from clients.models import (Client, Employee, InsurancePolicy, Notification, Product,
                            Renewal, Sale, Task)
from clients.services import insurance_sync


def _sale(customer, emp, *, years=1, policy_date, product="Health Insurance", ref=None):
    return Sale.objects.create(
        client=customer, employee=emp, product=product, product_ref=ref,
        amount=Decimal("100000"), policy_type="fresh", policy_years=years,
        status=Sale.STATUS_APPROVED, date=policy_date, policy_date=policy_date,
        policy_number=f"R{years}",
    )


class RenewalDateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(code="HEALTH_INS", defaults={"name": "Health Insurance"})
        cls.emp = Employee.objects.create(
            user=User.objects.create_user("rd_emp", password="x"), role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="RD Client")

    def test_single_year_renews_a_year_after_commencement(self):
        s = _sale(self.customer, self.emp, years=1, policy_date=datetime.date(2026, 7, 15), ref=self.health)
        self.assertEqual(s.coverage_end(), datetime.date(2027, 7, 15))
        self.assertEqual(s.next_renewal_date(datetime.date(2027, 1, 1)), datetime.date(2027, 7, 15))
        # after the first anniversary passes, it rolls to the next year
        self.assertEqual(s.next_renewal_date(datetime.date(2027, 8, 1)), datetime.date(2028, 7, 15))

    def test_multiyear_paid_through_term(self):
        s = _sale(self.customer, self.emp, years=3, policy_date=datetime.date(2026, 7, 15), ref=self.health)
        self.assertEqual(s.coverage_end(), datetime.date(2029, 7, 15))
        # no renewal during the paid term
        self.assertEqual(s.next_renewal_date(datetime.date(2027, 6, 1)), datetime.date(2029, 7, 15))


class PolicyRenewalReminderTests(TestCase):
    """Reminders walk the tracker, not the sales book — so the old book, which
    has no sale behind it, gets chased too."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = Employee.objects.create(
            user=User.objects.create_user("pr_owner", password="x"),
            role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="PR Client", mapped_to=cls.owner)
        # Back-filled from a renewal entry: no Sale exists for this policy.
        cls.policy = InsurancePolicy.objects.create(
            client=cls.customer, policy_number="OLDBOOK1", insurer="Star",
            insurance_type=InsurancePolicy.TYPE_HEALTH,
            sum_insured=Decimal("500000"), premium_amount=Decimal("18000"),
            start_date=datetime.date(2026, 7, 15), end_date=datetime.date(2027, 7, 15),
        )

    def _run_on(self, d):
        with mock.patch("clients.management.commands.renewal_reminders.timezone.localdate",
                        return_value=datetime.date(*d)):
            call_command("renewal_reminders")

    def _key(self):
        return f"polrenew:{self.policy.id}:2027-07-15"

    def test_policy_with_no_sale_still_reminds_the_mapped_employee(self):
        self._run_on((2027, 6, 15))                      # a month before
        task = Task.objects.filter(assign_group=self._key()).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.assigned_to, self.owner)
        self.assertEqual(task.due_date, datetime.date(2027, 7, 15))
        # a due_time is what arms the phone's on-device alarm (due_at_ms)
        self.assertIsNotNone(task.due_time)
        self.assertEqual(task.priority, Task.PRIORITY_HIGH)
        self.assertTrue(task.title.startswith("Renewal Reminder · PR Client · Health OLDBOOK1"))
        self.assertIn("5,00,000", task.description)       # sum assured
        self.assertIn("OLDBOOK1", task.description)
        self.assertTrue(Notification.objects.filter(recipient=self.owner.user).exists())

    def test_month_then_week_reuse_one_task(self):
        self._run_on((2027, 6, 15))                      # 30 days
        self._run_on((2027, 7, 8))                       # 7 days
        self.assertEqual(Task.objects.filter(assign_group=self._key()).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.owner.user).count(), 2)

    def test_silent_off_the_milestone_days(self):
        self._run_on((2027, 5, 1))
        self.assertFalse(Task.objects.filter(assign_group__startswith="polrenew:").exists())

    def test_collected_renewal_moves_the_policy_out_of_the_window(self):
        renewal = Renewal.objects.create(
            client=self.customer, product_type=Renewal.PRODUCT_TYPE_HEALTH,
            renewal_date=datetime.date(2027, 7, 15), frequency=Renewal.FREQUENCY_YEARLY,
            premium_amount=Decimal("18000"))
        insurance_sync.link_renewal_to_policy(
            renewal, selected_policy_id=self.policy.id)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.end_date, datetime.date(2028, 7, 15))
        # nothing left to chase for the 2027 cycle
        self._run_on((2027, 6, 15))
        self.assertFalse(Task.objects.filter(assign_group__startswith="polrenew:").exists())

    def test_multiyear_sale_expires_when_the_paid_term_ends(self):
        health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        seller = Employee.objects.create(
            user=User.objects.create_user("pr_seller", password="x"),
            role="employee", salary=0, active=True)
        sale = _sale(self.customer, seller, years=3,
                     policy_date=datetime.date(2026, 7, 15), ref=health)
        policy = insurance_sync.sync_policy_from_sale(sale)
        self.assertEqual(policy.end_date, datetime.date(2029, 7, 15))

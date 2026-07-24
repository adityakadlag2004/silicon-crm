"""Renewal date logic + reminders: single-year policies renew yearly (remind at
30/15/5 days to the mapped employee); multiyear policies stay silent until their
paid term ends."""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from clients.models import Client, Employee, Notification, Product, Sale, Task


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


class RenewalReminderCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.health, _ = Product.objects.get_or_create(code="HEALTH_INS", defaults={"name": "Health Insurance"})
        seller = Employee.objects.create(
            user=User.objects.create_user("rr_seller", password="x"), role="employee", salary=0, active=True)
        cls.owner = Employee.objects.create(
            user=User.objects.create_user("rr_owner", password="x"), role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="RR Client", mapped_to=cls.owner)
        # single-year policy commencing 2026-07-15 -> renews 2027-07-15
        cls.single = _sale(cls.customer, seller, years=1, policy_date=datetime.date(2026, 7, 15), ref=cls.health)
        # multiyear -> renews 2029-07-15, silent near-term
        cls.multi = _sale(cls.customer, seller, years=3, policy_date=datetime.date(2026, 7, 15), ref=cls.health)

    def _run_on(self, d):
        with mock.patch("clients.management.commands.renewal_reminders.timezone.localdate",
                        return_value=datetime.date(*d)):
            call_command("renewal_reminders")

    def test_reminds_mapped_owner_30_days_before(self):
        self._run_on((2027, 6, 15))  # 30 days before 2027-07-15
        key = f"renewal:{self.single.id}:2027-07-15"
        task = Task.objects.filter(assign_group=key).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.assigned_to, self.owner)
        self.assertEqual(task.due_date, datetime.date(2027, 7, 15))
        self.assertTrue(Notification.objects.filter(recipient=self.owner.user).exists())

    def test_fires_at_15_and_5_days_no_duplicate_task(self):
        self._run_on((2027, 6, 15))   # 30
        self._run_on((2027, 6, 30))   # 15
        self._run_on((2027, 7, 10))   # 5
        self.assertEqual(Task.objects.filter(assign_group=f"renewal:{self.single.id}:2027-07-15").count(), 1)

    def test_multiyear_silent_during_term(self):
        # around the single-year renewal window, the multiyear one must stay quiet
        self._run_on((2027, 6, 15))
        self.assertFalse(Task.objects.filter(assign_group__startswith=f"renewal:{self.multi.id}:").exists())

    def test_no_reminder_off_the_milestone_days(self):
        self._run_on((2027, 5, 1))  # ~75 days out
        self.assertFalse(Task.objects.filter(assign_group__startswith="renewal:").exists())

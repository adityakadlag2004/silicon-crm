"""External policies: what our clients hold elsewhere, and the reminders it drives.

Pins the schedule (premiums stop at the PPT, paid-up still matures, health
renews every term), the form's clean-up, the reminder tasks — tagged "External
Policy" everywhere they surface — and that none of it leaks into the tracker.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Client, Employee, ExternalPolicy, Task
from clients.services import calendar_feed
from clients.services.tasks import _add_months

XP = ExternalPolicy


def _policy(client, **kw):
    kw.setdefault("insurer", "LIC")
    kw.setdefault("start_date", date(2016, 4, 10))
    return ExternalPolicy.objects.create(client=client, **kw)


def _kinds(policy, start, end):
    return [(e["date"], e["kind"]) for e in policy.events(start, end)]


class ScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Client.objects.create(name="XP Schedule")

    def test_maturity_is_commencement_plus_term(self):
        p = _policy(self.customer, policy_term=20)
        self.assertEqual(p.maturity_date, date(2036, 4, 10))
        health = _policy(self.customer, policy_type=XP.TYPE_HEALTH, policy_term=1,
                         policy_number="H1")
        self.assertIsNone(health.maturity_date)     # renews, never matures

    def test_premiums_stop_at_the_premium_paying_term(self):
        p = _policy(self.customer, premium_paying_term=10, policy_term=20)
        # The 10th and last premium falls in 2025; 2026 is the PPT's end.
        self.assertEqual(_kinds(p, date(2025, 1, 1), date(2027, 12, 31)),
                         [(date(2025, 4, 10), "premium")])

    def test_paid_up_stops_premiums_but_still_matures(self):
        p = _policy(self.customer, premium_paying_term=15, policy_term=20,
                    status=XP.STATUS_PAID_UP)
        self.assertEqual(_kinds(p, date(2026, 1, 1), date(2036, 12, 31)),
                         [(date(2036, 4, 10), "maturity")])

    def test_a_dead_policy_produces_nothing(self):
        for status in (XP.STATUS_LAPSED, XP.STATUS_SURRENDERED, XP.STATUS_MATURED):
            p = _policy(self.customer, policy_term=20, status=status,
                        policy_number=f"D-{status}")
            self.assertEqual(p.events(date(2020, 1, 1), date(2040, 1, 1)), [])

    def test_health_renews_every_term(self):
        yearly = _policy(self.customer, policy_type=XP.TYPE_HEALTH, start_date=date(2024, 6, 1),
                         policy_number="H-1")
        self.assertEqual(_kinds(yearly, date(2025, 1, 1), date(2026, 12, 31)),
                         [(date(2025, 6, 1), "renewal"), (date(2026, 6, 1), "renewal")])
        three = _policy(self.customer, policy_type=XP.TYPE_HEALTH, start_date=date(2024, 6, 1),
                        policy_term=3, policy_number="H-3")
        self.assertEqual(_kinds(three, date(2025, 1, 1), date(2030, 12, 31)),
                         [(date(2027, 6, 1), "renewal"), (date(2030, 6, 1), "renewal")])

    def test_money_back_lands_inside_the_term_and_maturity_ends_it(self):
        p = _policy(self.customer, policy_type=XP.TYPE_MONEY_BACK, start_date=date(2010, 1, 1),
                    policy_term=20, premium_paying_term=15, payout_every_years=5,
                    payout_amount=Decimal("100000"), status=XP.STATUS_PAID_UP)
        self.assertEqual(_kinds(p, date(2011, 1, 1), date(2031, 1, 1)), [
            (date(2015, 1, 1), "payout"), (date(2020, 1, 1), "payout"),
            (date(2025, 1, 1), "payout"), (date(2030, 1, 1), "maturity"),
        ])

    def test_monthly_dates_stay_anchored_on_the_start_day(self):
        """Stepping from the previous date would turn 31 Jan into the 28th/29th for good."""
        p = _policy(self.customer, start_date=date(2020, 1, 31), premium_frequency=1)
        self.assertEqual([d for d, _ in _kinds(p, date(2024, 2, 1), date(2024, 4, 30))],
                         [date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30)])

    def test_pension_and_term_say_what_their_end_means(self):
        pension = _policy(self.customer, policy_type=XP.TYPE_PENSION, policy_term=10,
                          policy_number="P1")
        term = _policy(self.customer, policy_type=XP.TYPE_TERM, policy_term=10, policy_number="T1")
        self.assertIn("pension", pension.events(date(2026, 1, 1), date(2026, 12, 31))[-1]["label"])
        self.assertEqual(term.maturity_label, "Cover ends")
        # A term plan pays nothing when its cover ends — never show its cover as money in.
        self.assertEqual(term.events(date(2026, 1, 1), date(2026, 12, 31))[-1]["amount"], 0)


class FormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("xp_form", password="x")
        Employee.objects.create(user=cls.user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="XP Form")

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, **data):
        base = {"client": self.customer.pk, "policy_type": XP.TYPE_ENDOWMENT, "insurer": "LIC",
                "status": XP.STATUS_ACTIVE, "premium_frequency": 12, "start_date": "2016-04-10"}
        return self.client.post(reverse("clients:external_policy_add"), {**base, **data})

    def test_add_records_who_added_it_and_opens_the_policy(self):
        resp = self._post(policy_number="lic123 ", policy_term=20, premium_paying_term=15)
        p = ExternalPolicy.objects.get()
        self.assertRedirects(resp, reverse("clients:external_policy_detail", args=[p.pk]))
        self.assertEqual((p.created_by, p.policy_number), (self.user, "LIC123"))
        self.assertEqual(p.maturity_date, date(2036, 4, 10))
        self.assertEqual(p.sum_assured, 0)          # left blank → 0, not a crash

    def test_fields_the_type_does_not_use_are_blanked(self):
        self._post(policy_type=XP.TYPE_HEALTH, premium_paying_term=10, maturity_amount="500000",
                   payout_every_years=5, pension_amount="2000", premium_frequency=1)
        p = ExternalPolicy.objects.get()
        self.assertEqual((p.premium_paying_term, p.maturity_amount, p.payout_every_years,
                          p.pension_amount, p.premium_frequency), (None, 0, None, 0, 12))

    def test_same_number_twice_for_one_client_is_refused(self):
        self._post(policy_number="LIC123")
        resp = self._post(policy_number=" lic123")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already has an external policy with this number")
        self.assertEqual(ExternalPolicy.objects.count(), 1)

    def test_premium_term_longer_than_policy_term_is_refused(self):
        resp = self._post(premium_paying_term=25, policy_term=20)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ExternalPolicy.objects.exists())


class ScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("xp_admin", password="x")
        Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.other = User.objects.create_user("xp_other", password="x")
        Employee.objects.create(user=cls.other, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="XP Screens")
        due = timezone.localdate() + timedelta(days=20)
        cls.policy = _policy(cls.customer, insurer="Oldsure Life", policy_number="OLD1",
                             start_date=_add_months(due, -12), policy_term=20,
                             created_by=cls.admin)

    def test_dashboard_lists_it_tagged_with_its_next_premium_coming_up(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("clients:external_policy_list"))
        self.assertContains(resp, "Oldsure Life")
        self.assertContains(resp, ">External</span>")
        self.assertEqual([e["kind"] for e in resp.context["upcoming"]], ["premium"])
        # Tabs list only the types that hold something, each with its count.
        self.assertEqual([(t["label"], t["count"]) for t in resp.context["tabs"]],
                         [("All", 1), ("Endowment", 1)])

    def test_detail_and_profile_say_external_policy(self):
        self.client.force_login(self.other)
        detail = self.client.get(reverse("clients:external_policy_detail", args=[self.policy.pk]))
        self.assertContains(detail, "External Policy · not sold by us")
        profile = self.client.get(reverse("clients:client_profile", args=[self.customer.pk]))
        self.assertContains(profile, 'data-tab="external"')
        self.assertContains(profile, "External Policy")

    def test_it_never_reaches_the_trackers_own_book(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("clients:policy_list"))
        kpis = {k["label"]: k["value"] for k in resp.context["kpis"]}
        self.assertEqual(kpis["Policies"], 0)

    def test_only_an_admin_or_whoever_added_it_may_delete(self):
        url = reverse("clients:external_policy_delete", args=[self.policy.pk])
        self.client.force_login(self.other)
        self.client.post(url)
        self.assertTrue(ExternalPolicy.objects.filter(pk=self.policy.pk).exists())
        self.client.force_login(self.admin)
        self.client.post(url)
        self.assertFalse(ExternalPolicy.objects.filter(pk=self.policy.pk).exists())


class ReminderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Employee.objects.create(
            user=User.objects.create_user("xp_owner", password="x"),
            role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="XP Reminded", mapped_to=cls.owner)

    def _due_in(self, days, months=12, **kw):
        """A policy whose next premium falls `days` from today."""
        due = timezone.localdate() + timedelta(days=days)
        return _policy(self.customer, start_date=_add_months(due, -months),
                       premium_frequency=months, policy_number=f"R{days}-{months}", **kw)

    def test_one_tagged_task_for_the_mapped_employee_and_no_repeat(self):
        policy = self._due_in(20, policy_term=20)
        call_command("external_policy_reminders", stdout=StringIO())
        call_command("external_policy_reminders", stdout=StringIO())
        task = Task.objects.get()
        self.assertTrue(task.title.startswith("External Policy · Premium due · XP Reminded"))
        self.assertTrue(task.description.startswith("EXTERNAL POLICY"))
        self.assertEqual((task.assigned_to, task.client, task.source_kind, task.source_id),
                         (self.owner, self.customer, XP.TASK_SOURCE, policy.pk))

    def test_the_agenda_badge_says_external_policy(self):
        self._due_in(20, policy_term=20)
        call_command("external_policy_reminders", stdout=StringIO())
        now = timezone.now()
        items = calendar_feed._tasks(self.owner, now - timedelta(days=1), now + timedelta(days=40))
        self.assertEqual([i["source_label"] for i in items], ["External Policy"])

    def test_quarterly_waits_for_the_last_week_and_monthly_never_asks(self):
        self._due_in(20, months=3)
        self._due_in(5, months=1)
        call_command("external_policy_reminders", stdout=StringIO())
        self.assertFalse(Task.objects.exists())
        self._due_in(5, months=3)
        call_command("external_policy_reminders", stdout=StringIO())
        self.assertEqual(Task.objects.count(), 1)

    def test_deleting_the_policy_takes_its_reminders_with_it(self):
        policy = self._due_in(20, policy_term=20)
        call_command("external_policy_reminders", stdout=StringIO())
        policy.delete()
        self.assertFalse(Task.objects.exists())

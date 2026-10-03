"""Slowdowns found by timing every page on a production-sized book (3,000
clients, 6,000 sales, 15,000 tasks), each pinned so it cannot come back.

Run: .venv/bin/python manage.py test clients.test.test_performance_guards
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client as TestClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from clients import cron
from clients.middleware import TOUCH_KEY
from clients.models import (
    Client, Employee, IncentiveRule, IncentiveSlab, Product, Sale, Task, TaskChecklistItem,
)
from clients.services import incentives
from clients.views import calendar_views


def _emp(username, role="employee"):
    user = User.objects.create_user(username, password="x")
    return Employee.objects.create(user=user, role=role, salary=0, active=True)


def _http(emp):
    http = TestClient()
    http.force_login(emp.user)
    return http


def _session_writes(queries):
    return [q for q in queries if q["sql"].startswith("UPDATE \"django_session\"")]


class SessionRefreshTests(TestCase):
    """The session's 30-day window rolls once a day, not with a write per request."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = _emp("sr_emp")

    def test_requests_after_login_do_not_write_the_session(self):
        http = _http(self.emp)
        url = reverse("clients:notifications_json")
        with CaptureQueriesContext(connection) as ctx:
            http.get(url)
            http.get(url)
        self.assertEqual(_session_writes(ctx.captured_queries), [])

    def test_the_first_request_of_a_new_day_rolls_the_window(self):
        http = _http(self.emp)
        session = http.session
        session[TOUCH_KEY] = "2000-01-01"
        session.save()
        with CaptureQueriesContext(connection) as ctx:
            resp = http.get(reverse("clients:notifications_json"))
        self.assertEqual(len(_session_writes(ctx.captured_queries)), 1)
        self.assertIn("sessionid", resp.cookies)                  # cookie expiry rolls too
        self.assertEqual(http.session[TOUCH_KEY], timezone.localdate().isoformat())

    def test_an_anonymous_visit_creates_no_session(self):
        resp = TestClient().get(reverse("clients:login"))
        self.assertNotIn("sessionid", resp.cookies)


class TeamListTests(TestCase):
    def test_team_list_counts_clients_without_joining_sales(self):
        admin = _emp("tl_admin", role="admin")
        seller = _emp("tl_seller")
        product, _ = Product.objects.get_or_create(code="SIP", defaults={"name": "SIP"})
        for i in range(3):
            client = Client.objects.create(id=9800 + i, name=f"TL {i}", mapped_to=seller)
            for _ in range(2):
                Sale.objects.create(client=client, employee=seller, product="SIP",
                                    product_ref=product, amount=Decimal("1000"))
        with CaptureQueriesContext(connection) as ctx:
            resp = _http(admin).get(reverse("clients:team_list"))
        self.assertEqual(resp.status_code, 200)
        row = next(e for e in resp.context["employees"] if e.pk == seller.pk)
        self.assertEqual(row.client_count, 3)
        # Clients x sales in one GROUP BY took 11 s on a production-sized book.
        self.assertFalse([q for q in ctx.captured_queries
                          if '"clients_client"' in q["sql"] and '"clients_sale"' in q["sql"]
                          and '"clients_employee"."id"' in q["sql"]])


class AppTaskListTests(TestCase):
    def test_app_task_list_queries_flat_in_task_count(self):
        emp = _emp("at_emp")
        client = Client.objects.create(id=9810, name="AT Client")

        def make(n):
            for i in range(n):
                task = Task.objects.create(title=f"t{i}", assigned_to=emp,
                                           created_by=emp.user, client=client)
                TaskChecklistItem.objects.create(task=task, title="step")

        http = _http(emp)
        url = reverse("clients:app_tasks")
        make(2)
        http.get(url)
        with CaptureQueriesContext(connection) as few:
            http.get(url)
        make(10)
        with CaptureQueriesContext(connection) as many:
            data = http.get(url).json()
        self.assertEqual(len(data["tasks"]), 12)
        self.assertEqual(len(few), len(many), "app task list runs a query per task")


class AgendaCapTests(TestCase):
    def test_admin_agenda_caps_the_overdue_tail_and_says_so(self):
        admin = _emp("ag_cap_admin", role="admin")
        old = timezone.localdate() - timedelta(days=40)
        Task.objects.bulk_create([Task(title=f"old {i}", assigned_to=admin,
                                       due_date=old - timedelta(days=i)) for i in range(5)])
        with mock.patch.object(calendar_views, "AGENDA_LIMIT", 3):
            data = _http(admin).get(reverse("clients:dashboard_agenda_json")).json()
        tasks = [it for it in data["items"] if it["source"] == "task"]
        self.assertEqual(len(tasks), 3)
        self.assertTrue(data["capped"])
        # The newest overdue rows survive the cap, not the oldest.
        self.assertEqual({it["title"] for it in tasks}, {"old 0", "old 1", "old 2"})

    def test_a_small_agenda_is_not_marked_capped(self):
        emp = _emp("ag_cap_emp")
        Task.objects.create(title="one", assigned_to=emp, due_date=timezone.localdate())
        data = _http(emp).get(reverse("clients:dashboard_agenda_json")).json()
        self.assertFalse(data["capped"])


class IncentiveSlabTests(TestCase):
    def test_slab_lookups_use_the_prefetch(self):
        rule = IncentiveRule.objects.create(product="Life Insurance", unit_amount=Decimal("100000"),
                                            points_per_unit=Decimal("1750"),
                                            slab_mode=IncentiveRule.MODE_BONUS)
        for threshold, payout in ((100000, 1000), (300000, 3000)):
            IncentiveSlab.objects.create(rule=rule, threshold=Decimal(threshold),
                                         payout=Decimal(payout))
        rule = IncentiveRule.objects.prefetch_related("slabs").get(pk=rule.pk)
        with self.assertNumQueries(0):
            self.assertEqual(incentives.bonus_released_for(rule, Decimal("150000")), Decimal("1000"))
            self.assertEqual(incentives.next_rung(rule, Decimal("150000"))["slab"].threshold,
                             Decimal("300000"))
            incentives.rate_for_volume(rule, Decimal("150000"))


class AdminAddSaleTests(TestCase):
    def test_admin_add_sale_does_not_render_the_whole_client_book(self):
        admin = _emp("as_admin", role="admin")
        Client.objects.create(id=9820, name="Not In A Dropdown")
        html = _http(admin).get(reverse("clients:admin_add_sale")).content.decode()
        self.assertIn('id="client-search"', html)
        self.assertNotIn("Not In A Dropdown", html)
        self.assertIn('type="hidden" name="client"', html)


class EveryMinuteCronTests(TestCase):
    def test_both_minute_jobs_run_in_one_process_and_one_failure_spares_the_other(self):
        ran = []

        def fake(name, *a, **kw):
            ran.append(name)
            if name == cron.EVERY_MINUTE[0]:
                raise RuntimeError("boom")

        with mock.patch.object(cron, "call_command", side_effect=fake), \
                self.assertLogs("clients.cron", level="ERROR"):
            cron.every_minute()
        self.assertEqual(ran, list(cron.EVERY_MINUTE))

    def test_cronjobs_run_the_minute_jobs_through_the_shared_entry(self):
        from django.conf import settings
        minute = [job for job in settings.CRONJOBS if job[0] == "* * * * *"]
        self.assertEqual(minute, [("* * * * *", "clients.cron.every_minute")])
        call_command_jobs = {job[2][0] for job in settings.CRONJOBS if len(job) > 2}
        self.assertFalse(set(cron.EVERY_MINUTE) & call_command_jobs)

    def test_the_minute_jobs_still_run_for_real(self):
        cron.every_minute()            # no exception, nothing configured to push to

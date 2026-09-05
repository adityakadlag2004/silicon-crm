"""Query counts must not grow with the number of rows on a page.

These pages were all found doing one query per row on production (task cards
fetching their checklist, the client list fetching each mapped employee, the
employee dashboard aggregating per employee x product). A count that scales
with the book is invisible on a seeded dev database and crippling on 3,000
clients, so each test renders the page at two data sizes and asserts the
query count did not move.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client as TestClient, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Client, Employee, InsurancePolicy, Product, ProductMarginSlab, Sale, Task,
    TaskChecklistItem,
)


class QueryBudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("qb_admin", password="x")
        cls.admin = Employee.objects.create(
            user=cls.admin_user, role="admin", salary=0, active=True)
        cls.product, _ = Product.objects.get_or_create(
            code="SIP", defaults={"name": "SIP"})

    def _get(self, url_name, warm=False):
        http = TestClient()
        http.force_login(self.admin_user)
        url = reverse(f"clients:{url_name}")
        if warm:
            # First render of a process pays a few one-off lookups (content
            # types, permission cache). Measure a warm request, not that.
            http.get(url)
        with CaptureQueriesContext(connection) as ctx:
            resp = http.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp, len(ctx.captured_queries)

    def _make_tasks(self, n, prefix):
        for i in range(n):
            task = Task.objects.create(
                title=f"{prefix} task {i}",
                assigned_to=self.admin,
                created_by=self.admin_user,
            )
            # The card renders task.checklist_percent, which walks these.
            for j in range(2):
                TaskChecklistItem.objects.create(task=task, title=f"item {j}")

    def _make_clients(self, n, prefix):
        for i in range(n):
            Client.objects.create(name=f"{prefix} Client {i}", mapped_to=self.admin)

    def test_task_list_queries_flat_in_task_count(self):
        self._make_tasks(3, "a")
        _, few = self._get("task_dashboard", warm=True)
        self._make_tasks(12, "b")
        _, many = self._get("task_dashboard")
        self.assertEqual(few, many, "task list runs a query per card")

    def test_my_clients_queries_flat_in_client_count(self):
        self._make_clients(3, "A")
        _, few = self._get("my_clients", warm=True)
        self._make_clients(12, "B")
        _, many = self._get("my_clients")
        self.assertEqual(few, many, "client list runs a query per row")

    def test_employee_dashboard_queries_flat_in_employee_count(self):
        _, few = self._get("employee_dashboard", warm=True)
        for i in range(5):
            user = User.objects.create_user(f"qb_emp{i}", password="x")
            emp = Employee.objects.create(
                user=user, role="employee", salary=0, active=True)
            client = Client.objects.create(name=f"QB Client {i}", mapped_to=emp)
            Sale.objects.create(
                client=client, employee=emp, product=self.product.name,
                product_ref=self.product, amount=Decimal("10000"),
                status=Sale.STATUS_APPROVED, date=timezone.localdate(),
            )
        _, many = self._get("employee_dashboard")
        self.assertEqual(few, many, "dashboard aggregates per employee x product")

    def test_client_analysis_is_paginated(self):
        self._make_clients(60, "Z")
        resp, _ = self._get("client_analysis")
        # The whole book used to render in one 1.4 MB page.
        self.assertLessEqual(len(resp.context["clients"]), 50)
        self.assertTrue(resp.context["page_obj"].has_next)

    def test_client_analysis_still_marks_product_business(self):
        """Badges are attached to the page's instances, then rendered from the
        same ones — evaluating the page queryset twice would drop them."""
        buyer = Client.objects.create(name="Analysis Buyer", mapped_to=self.admin)
        Sale.objects.create(
            client=buyer, employee=self.admin, product=self.product.name,
            product_ref=self.product, amount=Decimal("50000"),
            status=Sale.STATUS_APPROVED, date=timezone.localdate(),
        )
        resp, _ = self._get("client_analysis")
        rows = {c.id: c for c in resp.context["clients"]}
        self.assertIn(buyer.id, rows)
        status_map = rows[buyer.id].dynamic_product_status_map
        self.assertTrue(status_map[f"product_{self.product.id}"])
        self.assertContains(resp, "Yes")

    def test_policy_list_is_paginated(self):
        holder = Client.objects.create(name="Policy Holder", mapped_to=self.admin)
        today = timezone.localdate()
        for i in range(60):
            InsurancePolicy.objects.create(
                client=holder, policy_number=f"QB{i:04d}",
                insurance_type=InsurancePolicy.TYPE_HEALTH,
                start_date=today, end_date=today + timedelta(days=200),
            )
        resp, _ = self._get("policy_list")
        # Was a flat [:300] slice — a 300-row page that still hid the rest.
        self.assertEqual(len(resp.context["policies"]), 50)
        self.assertTrue(resp.context["policy_page"].has_next)

    def test_margin_breakdown_reads_slabs_once(self):
        """margin_for resolves against prefetched slabs. It is called once per
        product (and per Fresh/Port bucket), so querying inside it meant a slab
        round trip per call; the prefetch must stay the only one."""
        from clients.views.reports import _month_margin_breakdown
        today = timezone.localdate()
        buyer = Client.objects.create(name="Margin Buyer", mapped_to=self.admin)

        def sell(product):
            ProductMarginSlab.objects.create(
                product=product, min_amount=0, max_amount=None, margin_percent=12)
            Sale.objects.create(
                client=buyer, employee=self.admin, product=product.name,
                product_ref=product, amount=Decimal("250000"),
                status=Sale.STATUS_APPROVED, date=today,
            )

        sell(self.product)
        for i in range(4):
            sell(Product.objects.create(name=f"QB Product {i}", code=f"QBP{i}"))

        _month_margin_breakdown(today.year, today.month)  # warm
        with CaptureQueriesContext(connection) as ctx:
            _month_margin_breakdown(today.year, today.month)
        slab_queries = [q for q in ctx.captured_queries
                        if "productmarginslab" in q["sql"].lower()]
        self.assertEqual(len(slab_queries), 1, "slabs are re-queried per margin_for call")

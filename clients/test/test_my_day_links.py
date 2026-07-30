"""The dashboard's "My day" rows are links, and each opens the set it counted.

A count that doesn't match the page it opens is worse than no count, so these
tests assert the two agree rather than just that the links exist.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import CalendarEvent, Client, Employee, Product, Sale, Task
from clients.services import sales as sales_service


class MyDayLinkTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.health, _ = Product.objects.get_or_create(
            code="HEALTH_INS", defaults={"name": "Health Insurance"})
        self.user = User.objects.create_user("emp", password="x")
        self.emp = Employee.objects.create(user=self.user, role="employee")
        self.client_rec = Client.objects.create(name="A Client", mapped_to=self.emp)
        self.client.force_login(self.user)

    def _dash(self):
        return self.client.get(reverse("clients:employee_dashboard"))

    def test_every_row_is_a_link(self):
        r = self._dash()
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for target in (f"{reverse('clients:task_my')}?range=due&amp;status=open",
                       reverse("clients:employee_calendar_page"),
                       f"{reverse('clients:all_sales')}?focus=renewals7",
                       f"{reverse('clients:all_sales')}?focus=emi"):
            self.assertIn(target, html, msg=f"missing link to {target}")

    def _task_titles(self, response):
        if "tasks" in response.context:
            return {t.title for t in response.context["tasks"]}
        return {t.title for col in response.context["columns"] for t in col["tasks"]}

    def test_tasks_link_opens_exactly_what_was_counted(self):
        Task.objects.create(title="Due today", assigned_to=self.emp,
                            created_by=self.user, due_date=self.today,
                            status=Task.STATUS_PENDING)
        done = Task.objects.create(title="Already done", assigned_to=self.emp,
                                   created_by=self.user, due_date=self.today,
                                   status=Task.STATUS_COMPLETED)
        Task.objects.create(title="Tomorrow", assigned_to=self.emp,
                            created_by=self.user,
                            due_date=self.today + timedelta(days=1),
                            status=Task.STATUS_PENDING)
        counted = self._dash().context["emp_overview"]["tasks_due"]
        self.assertEqual(counted, 1)

        listed = self.client.get(reverse("clients:task_my"),
                                 {"range": "due", "status": "open"})
        titles = self._task_titles(listed)
        self.assertEqual(titles, {"Due today"})
        self.assertNotIn(done.title, titles)

    def test_overdue_work_is_counted_and_listed(self):
        """The reported bug: every open task was late, so the row read zero."""
        Task.objects.create(title="Late by a day", assigned_to=self.emp,
                            created_by=self.user,
                            due_date=self.today - timedelta(days=1),
                            status=Task.STATUS_OVERDUE)
        Task.objects.create(title="Late by a week", assigned_to=self.emp,
                            created_by=self.user,
                            due_date=self.today - timedelta(days=7),
                            status=Task.STATUS_IN_PROGRESS)
        Task.objects.create(title="Next week", assigned_to=self.emp,
                            created_by=self.user,
                            due_date=self.today + timedelta(days=7),
                            status=Task.STATUS_PENDING)

        o = self._dash().context["emp_overview"]
        self.assertEqual(o["tasks_due"], 2)
        self.assertEqual(o["tasks_overdue"], 2)

        listed = self.client.get(reverse("clients:task_my"),
                                 {"range": "due", "status": "open"})
        self.assertEqual(self._task_titles(listed),
                         {"Late by a day", "Late by a week"})

    def test_the_row_says_how_many_are_overdue(self):
        Task.objects.create(title="Late", assigned_to=self.emp,
                            created_by=self.user,
                            due_date=self.today - timedelta(days=3),
                            status=Task.STATUS_OVERDUE)
        self.assertContains(self._dash(), "1 overdue")

    def test_a_completed_overdue_task_is_not_counted(self):
        Task.objects.create(title="Late but done", assigned_to=self.emp,
                            created_by=self.user,
                            due_date=self.today - timedelta(days=3),
                            status=Task.STATUS_COMPLETED)
        self.assertEqual(self._dash().context["emp_overview"]["tasks_due"], 0)

    def test_renewals_link_opens_exactly_what_was_counted(self):
        soon = Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("20000"),
            date=self.today - timedelta(days=360),
            policy_date=self.today - timedelta(days=360),
            status=Sale.STATUS_APPROVED, policy_type="fresh")
        Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("20000"),
            date=self.today - timedelta(days=200),
            policy_date=self.today - timedelta(days=200),
            status=Sale.STATUS_APPROVED, policy_type="fresh")

        counted = self._dash().context["emp_overview"]["renewals_7"]
        listed = self.client.get(reverse("clients:all_sales"), {"focus": "renewals7"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.context["sales"].object_list), counted)
        self.assertEqual(counted, 1)
        self.assertEqual(listed.context["sales"].object_list[0].pk, soon.pk)

    def test_emi_link_opens_exactly_what_was_counted(self):
        on_emi = Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("90000"),
            date=self.today.replace(day=1) - timedelta(days=1),
            policy_date=self.today, status=Sale.STATUS_APPROVED,
            policy_type="fresh", policy_years=3, emi_months=11)
        Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("30000"), date=self.today,
            policy_date=self.today, status=Sale.STATUS_APPROVED, policy_type="fresh")

        counted = self._dash().context["emp_overview"]["emis_due"]
        listed = self.client.get(reverse("clients:all_sales"), {"focus": "emi"})
        self.assertEqual(len(listed.context["sales"].object_list), counted)
        self.assertEqual(counted, 1)
        self.assertEqual(listed.context["sales"].object_list[0].pk, on_emi.pk)

    def test_focus_does_not_collapse_to_todays_sales_only(self):
        # The default view narrows to today when no filter is set; a focus link
        # must not be swallowed by that or it would always look empty.
        Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("90000"),
            date=self.today - timedelta(days=40), policy_date=self.today,
            status=Sale.STATUS_APPROVED, policy_type="fresh",
            policy_years=3, emi_months=11)
        listed = self.client.get(reverse("clients:all_sales"), {"focus": "emi"})
        self.assertEqual(len(listed.context["sales"].object_list), 1)

    def test_an_employee_only_sees_their_own_book(self):
        other = Employee.objects.create(
            user=User.objects.create_user("other", password="x"), role="employee")
        theirs = Client.objects.create(name="Their Client", mapped_to=other)
        Sale.objects.create(
            client=theirs, employee=other, product="Health Insurance",
            product_ref=self.health, amount=Decimal("90000"),
            date=self.today - timedelta(days=40), policy_date=self.today,
            status=Sale.STATUS_APPROVED, policy_type="fresh",
            policy_years=3, emi_months=11)
        listed = self.client.get(reverse("clients:all_sales"), {"focus": "emi"})
        self.assertEqual(len(listed.context["sales"].object_list), 0)

    def test_selectors_are_shared_so_counts_cannot_drift(self):
        # The dashboard count and the list filter must call the same function.
        Sale.objects.create(
            client=self.client_rec, employee=self.emp, product="Health Insurance",
            product_ref=self.health, amount=Decimal("90000"),
            date=self.today - timedelta(days=40), policy_date=self.today,
            status=Sale.STATUS_APPROVED, policy_type="fresh",
            policy_years=3, emi_months=11)
        ids = sales_service.emi_due_sale_ids(self.today, employee=self.emp)
        self.assertEqual(len(ids),
                         self._dash().context["emp_overview"]["emis_due"])

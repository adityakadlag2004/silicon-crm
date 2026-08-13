"""Guards for the shared form/UI behaviour that lives in base.html.

These are cheap presence checks on rendered HTML, not a browser test — they
only catch the script being dropped or the attribute being lost in an edit.

Run: .venv/bin/python manage.py test clients.test.test_ui_form_guards
"""
from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, InsurancePolicy


class SubmitGuardTests(TestCase):
    """Double-clicking Save on a laggy page used to POST twice and create
    duplicate clients."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("guard_admin", password="x", is_superuser=True)
        Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)

    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.user)

    def test_write_pages_carry_the_one_submit_guard(self):
        html = self.http.get(reverse("clients:add_client")).content.decode()
        self.assertIn("data-submitting", html)
        self.assertIn("dataset.submitting", html)

    def test_guard_leaves_get_filter_forms_alone(self):
        # The guard must skip GET forms, or the sales filter locks after one search.
        html = self.http.get(reverse("clients:all_sales")).content.decode()
        self.assertIn("'get').toLowerCase() !== 'post'", html)


class SearchableSelectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("claim_admin", password="x", is_superuser=True)
        Employee.objects.create(user=cls.user, role="admin", salary=0, active=True)
        client = Client.objects.create(id=9700, name="Policy Holder")
        InsurancePolicy.objects.create(client=client, policy_number="POL-1",
                                       insurance_type="health")

    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.user)

    def test_raise_claim_policy_picker_is_searchable(self):
        html = self.http.get(reverse("clients:raise_claim")).content.decode()
        self.assertIn("data-searchable", html)
        self.assertIn("select[data-searchable]", html)   # the enhancer shipped too

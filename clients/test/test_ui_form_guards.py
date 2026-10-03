"""Guards for the shared form/UI behaviour that every page loads from base.html.

These are cheap presence checks, not a browser test — they only catch the
script being dropped or the attribute being lost in an edit. The shell's
scripts are static files (cached by the browser rather than re-sent inside
every page), so a check is: the page links the script, and the script has it.

Run: .venv/bin/python manage.py test clients.test.test_ui_form_guards
"""
import pathlib

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, InsurancePolicy


FORMS_JS = "js/ki-shell-forms.js"


def _shell_page(http, url_name, script):
    """The rendered page plus the static script it must link."""
    html = http.get(reverse(f"clients:{url_name}")).content.decode()
    assert script in html, f"{url_name} does not load {script}"
    return html + pathlib.Path("static", script).read_text()


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
        html = _shell_page(self.http, "add_client", FORMS_JS)
        self.assertIn("data-submitting", html)
        self.assertIn("dataset.submitting", html)

    def test_guard_leaves_get_filter_forms_alone(self):
        # The guard must skip GET forms, or the sales filter locks after one search.
        html = _shell_page(self.http, "all_sales", FORMS_JS)
        self.assertIn("'get').toLowerCase() !== 'post'", html)

    def test_fetch_driven_posts_are_deduped_too(self):
        # Screens that post with fetch() (calendar events, bulk import, quick-add
        # client) never fire a submit event, so they need their own guard.
        html = _shell_page(self.http, "add_client", FORMS_JS)
        self.assertIn("window.fetch = function", html)
        self.assertIn("inflight", html)


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
        html = _shell_page(self.http, "raise_claim", FORMS_JS)
        self.assertIn("data-searchable", html)
        self.assertIn("select[data-searchable]", html)   # the enhancer shipped too

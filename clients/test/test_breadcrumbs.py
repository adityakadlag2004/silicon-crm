"""Auto-breadcrumbs: derived from the URL name, overridable by the view."""
from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse

from clients.context_processors import breadcrumbs
from clients.models import Client, Employee


class _FakeMatch:
    def __init__(self, name): self.url_name = name


class _FakeRequest:
    def __init__(self, name): self.resolver_match = _FakeMatch(name)


class BreadcrumbDerivationTests(TestCase):
    def test_landing_pages_get_one_crumb_not_a_self_nest(self):
        # "All Clients" is the Clients landing page — not "Clients > All Clients".
        self.assertEqual(breadcrumbs(_FakeRequest("all_clients"))["crumbs"],
                         [{"label": "All Clients"}])
        self.assertEqual(breadcrumbs(_FakeRequest("all_sales"))["crumbs"],
                         [{"label": "All Sales"}])

    def test_child_pages_get_section_then_page(self):
        crumbs = breadcrumbs(_FakeRequest("task_my"))["crumbs"]
        self.assertEqual(crumbs[0]["label"], "Tasks")
        self.assertIn("/tasks/", crumbs[0]["url"])
        self.assertEqual(crumbs[1]["label"], "My Tasks")

    def test_unmapped_and_dashboard_pages_get_nothing(self):
        self.assertEqual(breadcrumbs(_FakeRequest("admin_dashboard")), {})
        self.assertEqual(breadcrumbs(_FakeRequest("some_unmapped_name")), {})
        self.assertEqual(breadcrumbs(_FakeRequest(None)), {})

    def test_sections_without_a_landing_page_still_show_context(self):
        # "Settings" has no index page, so it appears as plain text, not a link.
        crumbs = breadcrumbs(_FakeRequest("firm_settings"))["crumbs"]
        self.assertEqual([c["label"] for c in crumbs], ["Settings", "Firm Settings"])
        self.assertNotIn("url", crumbs[0])
        # Reports likewise.
        rep = breadcrumbs(_FakeRequest("business_overview"))["crumbs"]
        self.assertEqual([c["label"] for c in rep], ["Reports", "Business Overview"])


class BreadcrumbRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("crumbuser", password="pw")
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        cls.c = Client.objects.create(id=9500, name="Test Client")

    def _get(self, url):
        tc = TC(); tc.force_login(self.u)
        return tc.get(url)

    def test_untouched_page_now_has_a_breadcrumb(self):
        # all_renewals was never edited for the theme — it gets one anyway.
        html = self._get(reverse("clients:all_renewals")).content.decode()
        self.assertIn("ki-crumb", html)
        self.assertIn("All Renewals", html)

    def test_view_supplied_crumbs_win_over_the_derived_ones(self):
        html = self._get(reverse("clients:client_profile", args=[9500])).content.decode()
        self.assertIn("ki-crumb", html)
        self.assertIn("Test Client", html)   # the view's own crumb, not "Profile"

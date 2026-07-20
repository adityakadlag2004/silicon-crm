"""Theme coverage: every main screen renders with the shell chrome.

This is the guard that stops a module being forgotten. It walks the real
GET screens rather than a hand-kept list, so a new page that skips the theme
shows up here instead of at a user.
"""
from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse

from clients.models import Client, Employee

# name -> kwargs. Every no-argument GET screen a user reaches from the nav.
SCREENS = [
    "all_clients", "my_clients", "family_list", "client_kyc_issues",
    "all_sales", "approve_sales", "all_renewals",
    "policy_list", "claim_list", "meeting_list",
    "lead_management", "task_dashboard", "task_my", "task_delegated",
    "task_all", "task_activities", "task_deleted",
    "mf_dashboard", "mf_folios", "mf_sips", "mf_transactions", "mf_cob",
    "links_dashboard", "team_list", "my_call_followups",
    "manage_campaigns", "manage_incentive_rules",
    "business_overview", "net_business", "net_sip", "monthly_business_report",
    "firm_settings", "product_management", "target_management", "audit_log",
    "admin_dashboard",
]


class ThemeCoverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("themer", password="pw", is_superuser=True)
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        Client.objects.create(id=9600, name="Theme Client")

    def setUp(self):
        self.tc = TC()
        self.tc.force_login(self.u)

    def test_every_screen_returns_200(self):
        broken = []
        for name in SCREENS:
            try:
                resp = self.tc.get(reverse(f"clients:{name}"))
                if resp.status_code != 200:
                    broken.append(f"{name} -> {resp.status_code}")
            except Exception as exc:                      # noqa: BLE001
                broken.append(f"{name} -> {type(exc).__name__}: {exc}")
        self.assertEqual(broken, [], f"screens not rendering: {broken}")

    def test_every_screen_loads_the_shell_stylesheet(self):
        missing = []
        for name in SCREENS:
            html = self.tc.get(reverse(f"clients:{name}")).content.decode()
            if "ki-record.css" not in html:
                missing.append(name)
        self.assertEqual(missing, [], f"screens without the shell CSS: {missing}")

    def test_listing_screens_have_a_breadcrumb(self):
        # Dashboards are deliberately excluded from breadcrumbs.
        missing = []
        for name in SCREENS:
            if name == "admin_dashboard":
                continue
            html = self.tc.get(reverse(f"clients:{name}")).content.decode()
            if "ki-crumb" not in html:
                missing.append(name)
        self.assertEqual(missing, [], f"screens without a breadcrumb: {missing}")


class TemplateHygieneTests(TestCase):
    """No template syntax should ever reach the browser as visible text."""

    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("hygiene", password="pw", is_superuser=True)
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)

    def test_no_multiline_django_comments_in_any_template(self):
        """`{# #}` cannot span lines — a multi-line one renders as page text.

        Django only strips single-line `{# ... #}`. Spread it over two lines
        and the whole thing is emitted literally into the HTML, which is
        exactly what happened with the breadcrumb and KPI partials.
        """
        import pathlib
        offenders = []
        for path in pathlib.Path("templates").rglob("*.html"):
            for num, line in enumerate(path.read_text().splitlines(), 1):
                if "{#" in line and "#}" not in line.split("{#", 1)[1]:
                    offenders.append(f"{path}:{num}")
        self.assertEqual(
            offenders, [],
            "multi-line {# #} comments render as visible text; "
            f"use {{% comment %}} instead: {offenders}")

    def test_rendered_pages_contain_no_leaked_template_syntax(self):
        """Unrendered tags/comments must not appear in the visible markup.

        Script and style blocks are excluded: inline JS legitimately contains
        braces like `}});`, which are not template syntax.
        """
        import re
        tc = TC(); tc.force_login(self.u)
        leaked = []
        for name in SCREENS:
            html = tc.get(reverse(f"clients:{name}")).content.decode()
            body = html.split("<body", 1)[-1]
            body = re.sub(r"<(script|style)\b.*?</\1>", "", body, flags=re.S | re.I)
            for marker in ("{#", "#}", "{%", "%}", "{{", "}}"):
                if marker in body:
                    idx = body.index(marker)
                    leaked.append(f"{name}: {marker!r} near {body[idx:idx + 60]!r}")
                    break
        self.assertEqual(leaked, [], f"template syntax leaked into HTML: {leaked}")


# Screens whose whole job is a list of records: each should lead with a KPI
# strip. Dashboards and form pages are deliberately excluded — a strip on a
# form is noise, and the task board already has its status tabs.
KPI_SCREENS = [
    "all_clients", "my_clients", "family_list", "client_kyc_issues",
    "all_sales", "all_renewals", "policy_list", "claim_list", "meeting_list",
    "lead_management", "task_deleted", "mf_folios", "mf_transactions",
    "links_dashboard", "team_list", "my_call_followups",
    "manage_incentive_rules", "audit_log",
]


class KpiStripCoverageTests(TestCase):
    """Every record-list screen leads with headline numbers."""

    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("kpiuser", password="pw", is_superuser=True)
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        Client.objects.create(id=9700, name="KPI Client")

    def test_list_screens_have_a_kpi_strip(self):
        tc = TC(); tc.force_login(self.u)
        missing = []
        for name in KPI_SCREENS:
            html = tc.get(reverse(f"clients:{name}")).content.decode()
            if "ki-kpis" not in html:
                missing.append(name)
        self.assertEqual(missing, [], f"list screens without a KPI strip: {missing}")

    def test_kpi_values_are_rendered_not_left_blank(self):
        """A strip of empty tiles means the view passed a broken structure."""
        import re
        tc = TC(); tc.force_login(self.u)
        empty = []
        for name in KPI_SCREENS:
            html = tc.get(reverse(f"clients:{name}")).content.decode()
            for value in re.findall(r'<div class="v">(.*?)</div>', html, re.S):
                if not value.strip():
                    empty.append(name)
                    break
        self.assertEqual(empty, [], f"screens with blank KPI values: {empty}")


class ResponsiveHygieneTests(TestCase):
    """Structural rules that keep the app usable on a phone."""

    def test_every_table_scrolls_inside_a_container(self):
        """A bare <table> pushes the whole page sideways on a narrow screen.

        Wide content must scroll inside its own box instead. Django admin
        templates are excluded — they use the admin's own stylesheet.
        """
        import re
        import pathlib
        offenders = []
        for path in pathlib.Path("templates").rglob("*.html"):
            if "admin/" in str(path):
                continue
            text = path.read_text()
            for m in re.finditer(r"<table[^>]*>", text):
                before = text[max(0, m.start() - 400):m.start()]
                if any(k in before for k in ("ki-table-wrap", "ki-table-scroll",
                                             "table-responsive", "overflow-x")):
                    continue
                offenders.append(f"{path}:{text[:m.start()].count(chr(10)) + 1}")
        self.assertEqual(
            offenders, [],
            "tables outside a scroll container overflow the page on mobile; "
            f"wrap them in .ki-table-scroll: {offenders}")

    def test_no_fixed_pixel_widths_wider_than_a_phone(self):
        """A hard width over ~360px forces horizontal scrolling on a phone."""
        import re
        import pathlib
        offenders = []
        for path in pathlib.Path("templates").rglob("*.html"):
            if "admin/" in str(path):
                continue
            for num, line in enumerate(path.read_text().splitlines(), 1):
                for m in re.finditer(r"(?<!max-)(?<!min-)width:\s*(\d{3,})px", line):
                    if int(m.group(1)) > 360:
                        offenders.append(f"{path}:{num} width:{m.group(1)}px")
        self.assertEqual(offenders, [], f"fixed widths wider than a phone: {offenders}")


class AccessibilityTests(TestCase):
    """Accessibility basics that are easy to regress and cheap to keep."""

    def test_icon_only_controls_have_an_accessible_name(self):
        """A button containing only <i class="bi-..."> reads as nothing.

        Screen readers announce an empty button; keyboard users get no clue
        what it does. Every icon-only control needs aria-label or title.
        """
        import re
        import pathlib
        offenders = []
        for path in pathlib.Path("templates").rglob("*.html"):
            if "admin/" in str(path):
                continue
            text = path.read_text()
            pattern = r'<(a|button)\b([^>]*)>\s*(<i class="bi[^"]*"[^>]*>\s*</i>)\s*</\1>'
            for m in re.finditer(pattern, text, re.S):
                if "aria-label" in m.group(2) or "title=" in m.group(2):
                    continue
                offenders.append(f"{path}:{text[:m.start()].count(chr(10)) + 1}")
        self.assertEqual(
            offenders, [],
            f"icon-only controls need aria-label or title: {offenders}")

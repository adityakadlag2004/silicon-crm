"""Two offices: every employee works out of one, and the Business Report can
be read one office at a time. A sale belongs to the seller's branch."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Branch, Client, Employee, Product, Sale
from clients.services import business_report


def _sip(sheet):
    """The test product's grand total (the migrations seed other columns)."""
    return dict(zip(sheet["products"], sheet["grand_vals"]))["Br SIP"]


class BranchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.head = Branch.objects.get(name="Head Office")  # migration 0142
        cls.second = Branch.objects.create(name="Nashik")
        cls.admin_user = User.objects.create_user("br_admin", password="x", first_name="Asha")
        cls.admin = Employee.objects.create(user=cls.admin_user, role="admin", active=True)
        cls.far = Employee.objects.create(user=User.objects.create_user("br_far", first_name="Ravi"),
                                          role="employee", branch=cls.second, active=True)
        sip = Product.objects.create(name="Br SIP", code="BRSIP", display_order=1)
        # The head-office client buys from the second office: the sale is Nashik's.
        cls.client_ = Client.objects.create(name="Br C", mapped_to=cls.admin)
        for emp, amt in ((cls.admin, "5000"), (cls.far, "2000"), (cls.far, "1000")):
            Sale.objects.create(client=cls.client_, employee=emp, product="Br SIP", product_ref=sip,
                                amount=Decimal(amt), status="approved", date=date(2026, 4, 9))

    def test_a_new_employee_lands_in_the_first_office(self):
        self.assertEqual(self.admin.branch, self.head)

    def test_sheet_reads_one_office(self):
        sheet = business_report.sheet(sel_year=2026, sel_month=4, branch=self.second)
        self.assertEqual([r["employee"] for r in sheet["rows"]], [self.far])
        self.assertEqual(_sip(sheet), Decimal("3000"))
        self.assertEqual(sheet["grand_accounts"], 0)  # the client is mapped to head office
        whole = business_report.sheet(sel_year=2026, sel_month=4)
        self.assertEqual(_sip(whole), Decimal("8000"))

    def test_report_page_filters_and_drops_the_firm_wide_awards(self):
        http = TestClient()
        http.force_login(self.admin_user)
        url = reverse("clients:monthly_business_report")
        whole = http.get(url, {"month": 4, "year": 2026})
        self.assertIn("celebration", whole.context)
        self.assertContains(whole, "All branches")
        one = http.get(url, {"month": 4, "year": 2026, "branch": self.second.pk})
        self.assertEqual(_sip(one.context), Decimal("3000"))
        self.assertNotIn("celebration", one.context)
        junk = http.get(url, {"month": 4, "year": 2026, "branch": "abc"})
        self.assertEqual(_sip(junk.context), Decimal("8000"))

    def test_team_edit_moves_an_employee(self):
        http = TestClient()
        http.force_login(self.admin_user)
        http.post(reverse("clients:team_edit", args=[self.far.pk]),
                  {"first_name": "Ravi", "role": "employee", "branch": self.head.pk})
        self.far.refresh_from_db()
        self.assertEqual(self.far.branch, self.head)

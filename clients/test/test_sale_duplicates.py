"""Duplicate-sale detection.

Employees forget they already booked a sale and enter it again — usually on a
different date, so the date can't be part of the match. Same client + product +
amount inside a two-month window is the signal; confirming is always allowed,
because a client really can buy the same thing twice.

Run: .venv/bin/python manage.py test clients.test.test_sale_duplicates
"""
from datetime import date, timedelta
from decimal import Decimal
import json

from django.contrib.auth.models import User
from django.test import Client as TestClient, TestCase
from django.urls import reverse

from clients.models import Client, Employee, Product, Sale
from clients.services import sales as sales_service


class _Setup(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("dup_admin", password="pw")
        cls.admin = Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user("dup_emp", password="pw")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.customer = Client.objects.create(name="Dup Customer", phone="9000000001")
        cls.other = Client.objects.create(name="Other Customer", phone="9000000002")
        cls.product = Product.objects.filter(is_active=True, parent__isnull=True).first()

    def _sale(self, **over):
        data = dict(client=self.customer, employee=self.emp, product=self.product.name,
                    amount=Decimal("50000"), date=date.today(), status=Sale.STATUS_APPROVED)
        data.update(over)
        return Sale.objects.create(**data)

    def _candidate(self, **over):
        data = dict(client=self.customer, employee=self.emp, product=self.product.name,
                    amount=Decimal("50000"), date=date.today())
        data.update(over)
        return Sale(**data)


class FindDuplicateTests(_Setup):
    def test_same_client_product_amount_is_flagged(self):
        existing = self._sale()
        self.assertEqual(sales_service.find_duplicate(self._candidate()), existing)

    def test_a_different_date_inside_the_window_still_matches(self):
        """The whole point: the second entry rarely carries the same date."""
        existing = self._sale(date=date.today() - timedelta(days=40))
        self.assertEqual(sales_service.find_duplicate(self._candidate()), existing)

    def test_outside_the_window_is_not_a_duplicate(self):
        self._sale(date=date.today() - timedelta(days=200))
        self.assertIsNone(sales_service.find_duplicate(self._candidate()))

    def test_different_amount_client_or_product_is_not_a_duplicate(self):
        self._sale()
        self.assertIsNone(sales_service.find_duplicate(self._candidate(amount=Decimal("50001"))))
        self.assertIsNone(sales_service.find_duplicate(self._candidate(client=self.other)))
        self.assertIsNone(sales_service.find_duplicate(self._candidate(product="Something Else")))

    def test_rejected_sales_do_not_block_a_re_entry(self):
        self._sale(status=Sale.STATUS_REJECTED)
        self.assertIsNone(sales_service.find_duplicate(self._candidate()))

    def test_a_colleagues_identical_sale_still_counts(self):
        """Two people entering the same sale is the case that hurts most."""
        existing = self._sale(employee=self.admin)
        self.assertEqual(sales_service.find_duplicate(self._candidate()), existing)

    def test_editing_a_sale_does_not_match_itself(self):
        existing = self._sale()
        self.assertIsNone(sales_service.find_duplicate(existing))


class WebConfirmFlowTests(_Setup):
    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.admin_user)

    def _post_data(self, **over):
        data = {
            "client": str(self.customer.id),
            "employee": str(self.emp.id),
            "product": self.product.name,
            "amount": "50000",
            "cover_amount": "",
            "date": date.today().isoformat(),
            "policy_type": "",
        }
        data.update(over)
        return data

    def test_duplicate_stops_at_a_confirmation_instead_of_saving(self):
        self._sale()
        before = Sale.objects.count()
        resp = self.http.post(reverse("clients:add_sale"), self._post_data())
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "sales/confirm_duplicate.html")
        self.assertEqual(Sale.objects.count(), before, "the sale must not be saved before confirming")

    def test_confirming_saves_the_second_sale(self):
        self._sale()
        before = Sale.objects.count()
        resp = self.http.post(reverse("clients:add_sale"),
                              self._post_data(confirm_duplicate="1"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Sale.objects.count(), before + 1)

    def test_a_first_sale_is_never_interrupted(self):
        before = Sale.objects.count()
        resp = self.http.post(reverse("clients:add_sale"), self._post_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Sale.objects.count(), before + 1)


class AppApiDuplicateTests(_Setup):
    def setUp(self):
        self.http = TestClient()
        self.http.force_login(self.emp_user)

    def _body(self, **over):
        body = {
            "client_id": self.customer.id,
            "product_id": self.product.id,
            "amount": "50000",
        }
        body.update(over)
        return json.dumps(body)

    def _post(self, body):
        return self.http.post("/clients/api/app/sales/create/", body,
                              content_type="application/json")

    def test_duplicate_is_refused_with_409_and_the_matched_sale(self):
        existing = self._sale()
        before = Sale.objects.count()
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertTrue(payload["duplicate"])
        self.assertEqual(payload["existing"]["id"], existing.id)
        self.assertEqual(Sale.objects.count(), before)

    def test_confirm_duplicate_lets_it_through(self):
        self._sale()
        before = Sale.objects.count()
        resp = self._post(self._body(confirm_duplicate=True))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Sale.objects.count(), before + 1)

    def test_a_first_sale_from_the_app_is_unaffected(self):
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 200)

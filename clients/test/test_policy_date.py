"""Insurance policy date: separate from the sale date, drives annual renewals."""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from clients.models import Client, Employee, Product, Sale
from clients.services import calendar_feed


def _setup_products():
    Product.objects.get_or_create(code="HEALTH_INS", defaults={"name": "Health Insurance", "domain": "both"})
    Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance", "domain": "both"})
    Product.objects.get_or_create(code="SIP", defaults={"name": "SIP", "domain": "sale"})


class PolicyDateModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_products()
        u = User.objects.create_user("pd_emp", password="pw")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=5001, name="Rahul Sharma")

    def _sale(self, product, **kw):
        d = dict(client=self.client_rec, employee=self.emp, product=product,
                 amount=10000, status=Sale.STATUS_APPROVED, date=date(2026, 3, 20))
        d.update(kw)
        return Sale.objects.create(**d)

    def test_is_insurance_only_for_health_and_life(self):
        self.assertTrue(self._sale("Health Insurance").is_insurance)
        self.assertTrue(self._sale("Life Insurance").is_insurance)
        self.assertFalse(self._sale("SIP").is_insurance)

    def test_renewal_basis_prefers_policy_date_over_sale_date(self):
        s = self._sale("Health Insurance", date=date(2026, 3, 20), policy_date=date(2026, 1, 5))
        self.assertEqual(s.renewal_basis, date(2026, 1, 5))

    def test_renewal_basis_falls_back_to_sale_date_for_legacy_rows(self):
        s = self._sale("Health Insurance", date=date(2026, 3, 20), policy_date=None)
        self.assertEqual(s.renewal_basis, date(2026, 3, 20))

    def test_policy_anniversary_is_next_annual_occurrence(self):
        s = self._sale("Life Insurance", policy_date=date(2024, 6, 15))
        self.assertEqual(s.policy_anniversary(date(2026, 1, 1)), date(2026, 6, 15))
        # On/after the anniversary rolls to next year.
        self.assertEqual(s.policy_anniversary(date(2026, 7, 1)), date(2027, 6, 15))
        # Exactly on the day counts as that day.
        self.assertEqual(s.policy_anniversary(date(2026, 6, 15)), date(2026, 6, 15))

    def test_29_feb_policy_renews_on_28_feb_in_common_years(self):
        s = self._sale("Health Insurance", policy_date=date(2024, 2, 29))
        self.assertEqual(s.policy_anniversary(date(2027, 1, 1)), date(2027, 2, 28))

    def test_non_insurance_has_no_anniversary(self):
        self.assertIsNone(self._sale("SIP", policy_date=date(2024, 1, 1)).policy_anniversary())


class PolicyDateFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_products()
        u = User.objects.create_user("pd_emp2", password="pw")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=5002, name="Vishal Bose")

    def _form(self, **overrides):
        from clients.forms import AdminSaleForm
        data = {
            "client": self.client_rec.id, "employee": self.emp.id,
            "product": "Health Insurance", "amount": "12000",
            "policy_type": "fresh", "date": "2026-03-20",
            "policy_date": "2026-01-05", "policy_number": "INS76123499",
        }
        data.update(overrides)
        return AdminSaleForm(data=data)

    def test_insurance_sale_requires_policy_date(self):
        form = self._form(policy_date="")
        self.assertFalse(form.is_valid())
        self.assertIn("policy_date", form.errors)

    def test_insurance_sale_with_policy_date_is_valid(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["policy_date"], date(2026, 1, 5))

    def test_non_insurance_sale_ignores_policy_date(self):
        form = self._form(product="SIP", policy_type="", policy_date="2026-01-05")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["policy_date"])

    def test_life_insurance_also_requires_policy_date(self):
        form = self._form(product="Life Insurance", policy_type="", policy_date="")
        self.assertFalse(form.is_valid())
        self.assertIn("policy_date", form.errors)


class InsuranceRenewalCalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _setup_products()
        u = User.objects.create_user("pd_emp3", password="pw")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=5003, name="Diya Sharma", mapped_to=cls.emp)

    def _sale(self, **kw):
        d = dict(client=self.client_rec, employee=self.emp, product="Health Insurance",
                 amount=10000, status=Sale.STATUS_APPROVED, date=date(2020, 3, 20),
                 policy_date=date(2020, 1, 10))
        d.update(kw)
        return Sale.objects.create(**d)

    def _renewals(self):
        start = timezone.make_aware(timezone.datetime(2026, 1, 1))
        end = timezone.make_aware(timezone.datetime(2026, 12, 31))
        items = calendar_feed.feed_items(self.emp, start=start, end=end,
                                         sources=["insurance_renewal"])
        return items

    def test_renewal_lands_on_policy_date_anniversary_not_sale_date(self):
        self._sale(policy_date=date(2020, 1, 10), date=date(2020, 3, 20))
        items = self._renewals()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["start"].date(), date(2026, 1, 10))  # policy, not sale
        self.assertEqual(items[0]["source"], "insurance_renewal")

    def test_legacy_sale_without_policy_date_uses_sale_date(self):
        self._sale(policy_date=None, date=date(2020, 3, 20))
        items = self._renewals()
        self.assertEqual(items[0]["start"].date(), date(2026, 3, 20))

    def test_only_approved_insurance_sales_generate_renewals(self):
        self._sale(status=Sale.STATUS_PENDING)
        self.assertEqual(self._renewals(), [])

    def test_non_insurance_sale_has_no_renewal(self):
        self._sale(product="SIP", policy_date=None, date=date(2020, 3, 20))
        self.assertEqual(self._renewals(), [])


class AddSaleEndToEndTests(TestCase):
    """Posting the real add-sale form persists policy_date on the Sale."""

    @classmethod
    def setUpTestData(cls):
        _setup_products()
        u = User.objects.create_user("pd_admin", password="pw")
        cls.admin_emp = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user("pd_seller", password="pw")
        cls.seller = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=5004, name="Aarav Malhotra")

    def test_posting_insurance_sale_saves_policy_date(self):
        from django.test import Client as TC
        from django.urls import reverse
        tc = TC(); tc.force_login(self.admin_emp.user)
        resp = tc.post(reverse("clients:add_sale"), {
            "client": self.client_rec.id, "employee": self.seller.id,
            "product": "Health Insurance", "amount": "12000",
            "policy_type": "fresh", "date": "2026-03-20",
            "policy_date": "2026-01-05", "cover_amount": "500000",
            "policy_number": "INS76123499",
        })
        self.assertIn(resp.status_code, (302, 200))
        sale = Sale.objects.filter(client=self.client_rec, product="Health Insurance").first()
        self.assertIsNotNone(sale, "sale was not created")
        self.assertEqual(sale.policy_date, date(2026, 1, 5))
        self.assertNotEqual(sale.policy_date, sale.date)

    def test_posting_insurance_sale_without_policy_date_is_rejected(self):
        from django.test import Client as TC
        from django.urls import reverse
        tc = TC(); tc.force_login(self.admin_emp.user)
        before = Sale.objects.count()
        resp = tc.post(reverse("clients:add_sale"), {
            "client": self.client_rec.id, "employee": self.seller.id,
            "product": "Health Insurance", "amount": "12000",
            "policy_type": "fresh", "date": "2026-03-20", "policy_date": "",
        })
        self.assertEqual(resp.status_code, 200)   # re-rendered with errors
        self.assertEqual(Sale.objects.count(), before)


class AppApiPolicyDateTests(TestCase):
    """The mobile Add Sale endpoint must enforce exactly what the web form
    does. It used to accept insurance sales with neither field, which left
    every renewal reminder measured from the approval date and the tracker
    policy stamped with a placeholder "SALE-<pk>" number."""

    @classmethod
    def setUpTestData(cls):
        _setup_products()
        au = User.objects.create_user("app_pd_admin", password="pw")
        cls.admin = Employee.objects.create(user=au, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=5010, name="Neha Kulkarni")
        cls.health = Product.objects.get(code="HEALTH_INS")
        cls.sip = Product.objects.get(code="SIP")

    def _post(self, **body):
        import json
        from django.test import Client as TC
        from django.urls import reverse
        tc = TC()
        tc.force_login(self.admin.user)
        payload = {"client_id": self.client_rec.id, "amount": "12000"}
        payload.update(body)
        return tc.post(
            reverse("clients:app_sale_create"),
            data=json.dumps(payload), content_type="application/json",
        )

    def test_insurance_sale_without_policy_date_is_rejected(self):
        before = Sale.objects.count()
        resp = self._post(product_id=self.health.id, policy_type="fresh", policy_number="PN1")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("policy date", resp.json()["error"].lower())
        self.assertEqual(Sale.objects.count(), before)

    def test_insurance_sale_without_policy_number_is_rejected(self):
        before = Sale.objects.count()
        resp = self._post(product_id=self.health.id, policy_type="fresh", policy_date="2026-01-05")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("policy number", resp.json()["error"].lower())
        self.assertEqual(Sale.objects.count(), before)

    def test_health_sale_without_policy_type_is_rejected(self):
        resp = self._post(product_id=self.health.id, policy_date="2026-01-05", policy_number="PN1")
        self.assertEqual(resp.status_code, 400)

    def test_insurance_sale_saves_both_and_drives_renewal_basis(self):
        resp = self._post(
            product_id=self.health.id, policy_type="fresh",
            policy_date="2026-01-05", policy_number="ins76123499",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        sale = Sale.objects.get(pk=resp.json()["id"])
        self.assertEqual(sale.policy_date, date(2026, 1, 5))
        self.assertEqual(sale.policy_number, "INS76123499")
        self.assertEqual(sale.renewal_basis, date(2026, 1, 5))
        self.assertNotEqual(sale.renewal_basis, sale.date)

    def test_tracker_policy_uses_the_real_number_not_a_placeholder(self):
        from clients.models import InsurancePolicy
        resp = self._post(
            product_id=self.health.id, policy_type="fresh",
            policy_date="2026-01-05", policy_number="INS76123499",
        )
        policy = InsurancePolicy.objects.get(source_sale_id=resp.json()["id"])
        self.assertEqual(policy.policy_number, "INS76123499")
        self.assertEqual(policy.start_date, date(2026, 1, 5))

    def test_non_insurance_sale_needs_neither_field(self):
        resp = self._post(product_id=self.sip.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        sale = Sale.objects.get(pk=resp.json()["id"])
        self.assertIsNone(sale.policy_date)
        self.assertEqual(sale.policy_number, "")


class OldAppCompatibilityTests(TestCase):
    """Devices on an APK older than v4.23 have no policy date/number fields
    and omit the keys entirely. Telling them to "enter the policy date" points
    at a box that isn't on their screen — the error has to say "update"."""

    @classmethod
    def setUpTestData(cls):
        _setup_products()
        u = User.objects.create_user("oldapp_admin", password="pw")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=5020, name="Vikram Joshi")
        cls.health = Product.objects.get(code="HEALTH_INS")
        cls.sip = Product.objects.get(code="SIP")

    def _post(self, payload):
        import json
        from django.test import Client as TC
        from django.urls import reverse
        tc = TC()
        tc.force_login(self.admin.user)
        return tc.post(reverse("clients:app_sale_create"),
                       data=json.dumps(payload), content_type="application/json")

    def test_old_app_insurance_sale_is_told_to_update(self):
        # Exactly what pre-4.23 builds send: no policy_date/policy_number keys.
        resp = self._post({
            "client_id": self.client_rec.id, "product_id": self.health.id,
            "ppt": "", "amount": "12000", "cover_amount": "", "policy_type": "fresh",
            "policy_years": 1, "emi_months": 0,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("update the app", resp.json()["error"].lower())

    def test_new_app_blank_field_still_gets_the_field_level_message(self):
        # v4.23+ always sends the keys, blank when the user skipped them.
        resp = self._post({
            "client_id": self.client_rec.id, "product_id": self.health.id,
            "amount": "12000", "policy_type": "fresh",
            "policy_date": "", "policy_number": "",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("policy date", resp.json()["error"].lower())
        self.assertNotIn("update the app", resp.json()["error"].lower())

    def test_old_app_can_still_book_non_insurance_sales(self):
        # The whole point: don't break SIP/other business for stale devices.
        resp = self._post({
            "client_id": self.client_rec.id, "product_id": self.sip.id,
            "amount": "5000",
        })
        self.assertEqual(resp.status_code, 200, resp.content)

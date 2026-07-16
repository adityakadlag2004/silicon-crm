"""Client KYC: PAN required on add, KYC Issues screen (inline PAN entry,
role scoping), duplicate merge/safe-delete, and the live MF profile summary.

Run: .venv/bin/python manage.py test clients.test.test_client_kyc -v 2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client as TestClient
from django.test import TestCase
from django.urls import reverse

from clients.forms import ClientForm
from clients.models import (
    Client,
    Employee,
    MutualFundFolio,
    MutualFundTransaction,
    Sale,
)
from clients.services import client_merge, rta_feed


def _form_data(**overrides):
    data = {
        "name": "Test Person", "phone": "9876543210", "email": "t@x.com",
        "pan": "ABCDE1234F", "lumsum_investment": "0",
    }
    data.update(overrides)
    return data


class PanRequiredTests(TestCase):
    def test_new_client_requires_pan(self):
        form = ClientForm(data=_form_data(pan=""))
        self.assertFalse(form.is_valid())
        self.assertIn("pan", form.errors)

    def test_invalid_pan_rejected(self):
        form = ClientForm(data=_form_data(pan="12345ABCDE"))
        self.assertFalse(form.is_valid())
        self.assertIn("pan", form.errors)

    def test_valid_pan_normalized(self):
        form = ClientForm(data=_form_data(pan=" abcde 1234 f "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["pan"], "ABCDE1234F")

    def test_editing_old_client_without_pan_is_allowed(self):
        client = Client.objects.create(name="Legacy", phone="1", email="l@x.com")
        form = ClientForm(data=_form_data(pan=""), instance=client)
        self.assertTrue(form.is_valid(), form.errors)


class KycIssuesScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for role in ("admin", "employee"):
            user = User.objects.create_user(username=f"kyc_{role}", password="x")
            setattr(cls, f"{role}_emp", Employee.objects.create(user=user, role=role, salary=0, active=True))
        other_user = User.objects.create_user(username="kyc_other", password="x")
        cls.other_emp = Employee.objects.create(user=other_user, role="employee", salary=0, active=True)

        cls.mine = Client.objects.create(name="Mine NoPan", mapped_to=cls.employee_emp)
        cls.others = Client.objects.create(name="Others NoPan", mapped_to=cls.other_emp)
        Client.objects.create(name="Has Pan", pan="ABCDE1234F", mapped_to=cls.employee_emp)

    def _login(self, role):
        web = TestClient()
        web.force_login(User.objects.get(username=f"kyc_{role}"))
        return web

    def test_employee_sees_only_their_missing_pan_clients(self):
        resp = self._login("employee").get(reverse("clients:client_kyc_issues"))
        self.assertContains(resp, "Mine NoPan")
        self.assertNotContains(resp, "Others NoPan")

    def test_admin_sees_all_and_duplicates_section(self):
        resp = self._login("admin").get(reverse("clients:client_kyc_issues"))
        self.assertContains(resp, "Mine NoPan")
        self.assertContains(resp, "Others NoPan")
        self.assertContains(resp, "Possible duplicate profiles")

    def test_inline_pan_update_and_bad_format(self):
        web = self._login("employee")
        web.post(reverse("clients:client_kyc_update_pan", args=[self.mine.id]), {"pan": "fghij5678k"})
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.pan, "FGHIJ5678K")

        resp = web.post(reverse("clients:client_kyc_update_pan", args=[self.mine.id]),
                        {"pan": "notapan"}, follow=True)
        self.assertContains(resp, "valid PAN")

    def test_employee_cannot_update_others_client(self):
        resp = self._login("employee").post(
            reverse("clients:client_kyc_update_pan", args=[self.others.id]), {"pan": "FGHIJ5678K"})
        self.assertEqual(resp.status_code, 403)

    def test_pan_save_triggers_folio_relink(self):
        MutualFundFolio.objects.create(folio_number="42", amc_name="H", pan="FGHIJ5678K")
        self._login("employee").post(
            reverse("clients:client_kyc_update_pan", args=[self.mine.id]), {"pan": "FGHIJ5678K"})
        folio = MutualFundFolio.objects.get(folio_number="42")
        self.assertEqual(folio.client_id, self.mine.id)

    def test_dashboard_banner_count(self):
        resp = self._login("employee").get(reverse("clients:employee_dashboard"))
        self.assertContains(resp, "KYC Issues")


class MergeDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        admin_user = User.objects.create_user(username="merge_admin", password="x")
        cls.admin_emp = Employee.objects.create(user=admin_user, role="admin", salary=0, active=True)
        cls.keep = Client.objects.create(name="Rahul Sharma", phone="9876543210")
        cls.dup = Client.objects.create(name="Rahul Sharma", phone="9876543210",
                                        pan="ABCDE1234F", email="r@x.com")

    def _login(self):
        web = TestClient()
        web.force_login(User.objects.get(username="merge_admin"))
        return web

    def test_merge_moves_records_and_fills_fields(self):
        Sale.objects.create(client=self.dup, employee=self.admin_emp, product="SIP",
                            amount=Decimal("5000"))
        folio = MutualFundFolio.objects.create(folio_number="77", amc_name="H", client=self.dup)

        moved = client_merge.merge_clients(self.keep, self.dup)
        self.assertIn("sales", moved)
        self.assertFalse(Client.objects.filter(id=self.dup.id).exists())
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.pan, "ABCDE1234F")  # filled from duplicate
        folio.refresh_from_db()
        self.assertEqual(folio.client_id, self.keep.id)
        self.assertEqual(self.keep.sales.count(), 1)

    def test_merge_view_handles_group(self):
        resp = self._login().post(reverse("clients:client_merge"),
                                  {"keep_id": self.keep.id, "remove_id": [str(self.dup.id)]},
                                  follow=True)
        self.assertContains(resp, "Merged into")
        self.assertFalse(Client.objects.filter(id=self.dup.id).exists())

    def test_safe_delete_blocked_when_records_exist(self):
        Sale.objects.create(client=self.dup, employee=self.admin_emp, product="SIP",
                            amount=Decimal("5000"))
        resp = self._login().post(reverse("clients:client_safe_delete", args=[self.dup.id]), follow=True)
        self.assertContains(resp, "merge it into the correct profile")
        self.assertTrue(Client.objects.filter(id=self.dup.id).exists())

    def test_safe_delete_works_on_empty_profile(self):
        self._login().post(reverse("clients:client_safe_delete", args=[self.dup.id]))
        self.assertFalse(Client.objects.filter(id=self.dup.id).exists())

    def test_duplicates_grouping(self):
        web = self._login()
        resp = web.get(reverse("clients:client_kyc_issues"))
        self.assertContains(resp, "Same phone")


class MfSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.client_obj = Client.objects.create(name="Investor", pan="ABCDE1234F")
        cls.folio = MutualFundFolio.objects.create(folio_number="9", amc_name="H", client=cls.client_obj)

    def _txn(self, key, **kw):
        defaults = dict(folio=self.folio, txn_type="SIP Purchase", amount=Decimal("5000"),
                        units=Decimal("50"), nav=Decimal("100"),
                        trade_date=date.today() - timedelta(days=5), dedupe_key=key)
        defaults.update(kw)
        return MutualFundTransaction.objects.create(**defaults)

    def test_summary_math(self):
        self._txn("a", scheme_name="Scheme A")
        self._txn("b", scheme_name="Scheme A", txn_type="Redemption",
                  amount=Decimal("2000"), units=Decimal("10"), nav=Decimal("200"))
        summary = rta_feed.mf_summary_for_client(self.client_obj)
        self.assertEqual(summary["monthly_sip"], Decimal("5000"))
        self.assertEqual(summary["inflow_12m"], Decimal("5000"))
        self.assertEqual(summary["outflow_12m"], Decimal("2000"))
        # 50 bought − 10 redeemed = 40 units × last NAV 200 = 8000
        self.assertEqual(summary["est_value"], Decimal("8000"))

    def test_no_transactions_returns_none(self):
        stranger = Client.objects.create(name="Empty")
        self.assertIsNone(rta_feed.mf_summary_for_client(stranger))

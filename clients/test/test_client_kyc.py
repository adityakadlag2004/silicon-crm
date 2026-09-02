"""Client KYC: PAN required on add, KYC Issues screen (inline PAN entry,
role scoping) and duplicate merge/safe-delete.

Run: .venv/bin/python manage.py test clients.test.test_client_kyc -v 2
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client as TestClient
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from clients.forms import ClientForm
from clients.models import Client, Employee, Sale
from clients.services import client_merge


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
        moved = client_merge.merge_clients(self.keep, self.dup)
        self.assertIn("sales", moved)
        self.assertFalse(Client.objects.filter(id=self.dup.id).exists())
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.pan, "ABCDE1234F")  # filled from duplicate
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

    def test_missing_pan_list_is_paginated(self):
        from clients.views.kyc import MISSING_PAN_PER_PAGE
        # Client.id is assigned in save(), so create() — not bulk_create()
        for i in range(MISSING_PAN_PER_PAGE + 25):
            Client.objects.create(name=f"NoPan {i:03d}", phone="9000000000")
        resp = self._login().get(reverse("clients:client_kyc_issues"))
        self.assertEqual(len(resp.context["missing"]), MISSING_PAN_PER_PAGE)
        # the header count stays the true total, not the page size
        self.assertGreater(resp.context["missing_total"], MISSING_PAN_PER_PAGE)
        self.assertTrue(resp.context["page_obj"].has_next())

        page2 = self._login().get(reverse("clients:client_kyc_issues"), {"page": 2})
        self.assertEqual(page2.context["page_obj"].number, 2)
        self.assertNotEqual([c.id for c in page2.context["missing"]],
                            [c.id for c in resp.context["missing"]])

    def test_missing_pan_search_filters(self):
        Client.objects.create(name="Zzz Findme", phone="9111111111")
        resp = self._login().get(reverse("clients:client_kyc_issues"), {"q": "Findme"})
        names = [c.name for c in resp.context["missing"]]
        self.assertEqual(names, ["Zzz Findme"])
        self.assertEqual(resp.context["missing_total"], 1)

    def test_pan_save_returns_to_same_page_and_search(self):
        target = Client.objects.create(name="Paginated Guy", phone="9222222222")
        resp = self._login().post(
            reverse("clients:client_kyc_update_pan", args=[target.id]),
            {"pan": "AAAPZ1234C", "page": "3", "q": "guy"},
        )
        self.assertIn("page=3", resp.url)
        self.assertIn("q=guy", resp.url)
        target.refresh_from_db()
        self.assertEqual(target.pan, "AAAPZ1234C")

    def test_bulk_counts_match_per_client_counts(self):
        Sale.objects.create(client=self.dup, employee=self.admin_emp, product="SIP",
                            amount=Decimal("5000"))
        bulk = client_merge.business_record_counts_bulk([self.keep, self.dup])
        self.assertEqual(bulk[self.dup.id], client_merge.business_record_counts(self.dup))
        self.assertEqual(bulk[self.keep.id], client_merge.business_record_counts(self.keep))
        self.assertEqual(bulk[self.dup.id]["sales"], 1)
        self.assertEqual(bulk[self.keep.id], {})  # no records — empty, not missing

    def test_bulk_counts_accepts_ids_and_empty_input(self):
        Sale.objects.create(client=self.dup, employee=self.admin_emp, product="SIP",
                            amount=Decimal("5000"))
        by_id = client_merge.business_record_counts_bulk([self.dup.id])
        self.assertEqual(by_id[self.dup.id]["sales"], 1)
        self.assertEqual(client_merge.business_record_counts_bulk([]), {})

    def test_bulk_counts_query_count_is_flat(self):
        """Guards the N+1 this replaced: 20 clients must cost the same as 2."""
        extra = [Client.objects.create(name=f"Dup {i}", phone="9876543210")
                 for i in range(18)]
        with CaptureQueriesContext(connection) as small:
            client_merge.business_record_counts_bulk([self.keep, self.dup])
        with CaptureQueriesContext(connection) as large:
            client_merge.business_record_counts_bulk([self.keep, self.dup] + extra)
        self.assertEqual(len(small.captured_queries), len(large.captured_queries))

"""Insurance Tracker sync: sales and renewals feed the tracker automatically."""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from clients.models import Client, Employee, InsurancePolicy, Product, Renewal, Sale
from clients.services import insurance_sync, sales as sales_service


def _products():
    Product.objects.get_or_create(code="HEALTH_INS", defaults={"name": "Health Insurance", "domain": "both"})
    Product.objects.get_or_create(code="LIFE_INS", defaults={"name": "Life Insurance", "domain": "both"})
    Product.objects.get_or_create(code="SIP", defaults={"name": "SIP", "domain": "sale"})


class SaleToPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _products()
        u = User.objects.create_user("is_admin", password="pw")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=6001, name="Vishal Bose")

    def _sale(self, product="Health Insurance", **kw):
        d = dict(client=self.client_rec, employee=self.admin, product=product,
                 amount=12000, cover_amount=500000, policy_date=date(2026, 1, 5),
                 date=date(2026, 3, 20))
        d.update(kw)
        return Sale(**d)

    def test_approved_insurance_sale_creates_a_tracker_policy(self):
        sale = self._sale()
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        policy = InsurancePolicy.objects.filter(source_sale=sale).first()
        self.assertIsNotNone(policy)
        self.assertEqual(policy.insurance_type, InsurancePolicy.TYPE_HEALTH)
        # Start date is the policy date, not the sale date.
        self.assertEqual(policy.start_date, date(2026, 1, 5))
        self.assertEqual(policy.end_date, date(2027, 1, 5))
        self.assertEqual(policy.premium_amount, 12000)
        self.assertEqual(policy.sum_insured, 500000)

    def test_sync_is_idempotent(self):
        sale = self._sale()
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        insurance_sync.sync_policy_from_sale(sale)
        insurance_sync.sync_policy_from_sale(sale)
        self.assertEqual(InsurancePolicy.objects.filter(source_sale=sale).count(), 1)

    def test_life_sale_creates_life_policy(self):
        sale = self._sale(product="Life Insurance", policy_type="")
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        self.assertEqual(sale.policy.insurance_type, InsurancePolicy.TYPE_LIFE)

    def test_non_insurance_sale_creates_no_policy(self):
        sale = self._sale(product="SIP", cover_amount=None, policy_date=None, policy_type="")
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        self.assertFalse(InsurancePolicy.objects.filter(source_sale=sale).exists())

    def test_pending_sale_creates_no_policy_until_approved(self):
        sale = self._sale()
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=False)
        self.assertFalse(InsurancePolicy.objects.filter(source_sale=sale).exists())
        # Approving it later syncs.
        sales_service.approve_sale(sale, self.admin.user)
        self.assertTrue(InsurancePolicy.objects.filter(source_sale=sale).exists())

    def test_rejecting_removes_the_untouched_policy(self):
        sale = self._sale()
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        self.assertTrue(InsurancePolicy.objects.filter(source_sale=sale).exists())
        sales_service.reject_sale(sale, self.admin.user, "duplicate")
        self.assertFalse(InsurancePolicy.objects.filter(source_sale=sale).exists())

    def test_rejecting_keeps_a_curated_policy_but_detaches_it(self):
        sale = self._sale()
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        policy = sale.policy
        policy.insurer = "ICICI Lombard"          # someone curated it
        policy.policy_number = "REAL123"
        policy.save()
        sales_service.reject_sale(sale, self.admin.user)
        policy.refresh_from_db()
        self.assertIsNone(policy.source_sale_id)   # detached, not deleted
        self.assertTrue(InsurancePolicy.objects.filter(pk=policy.pk).exists())


class RenewalToPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _products()
        u = User.objects.create_user("is_emp", password="pw")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=6002, name="Rahul Sharma")

    def _renewal(self, ptype=Renewal.PRODUCT_TYPE_HEALTH, **kw):
        d = dict(client=self.client_rec, employee=self.emp, product_type=ptype,
                 renewal_date=date(2026, 2, 10), frequency="yearly", premium_amount=8000)
        d.update(kw)
        return Renewal.objects.create(**d)

    def test_renewal_backfills_a_policy_when_client_has_none(self):
        r = self._renewal()
        policy = insurance_sync.sync_policy_from_renewal(r)
        self.assertIsNotNone(policy)
        self.assertEqual(policy.insurance_type, InsurancePolicy.TYPE_HEALTH)
        self.assertEqual(policy.start_date, date(2026, 2, 10))
        self.assertEqual(policy.client, self.client_rec)

    def test_renewal_does_not_duplicate_an_existing_policy(self):
        InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="EXIST1", insurer="HDFC",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        r = self._renewal()
        result = insurance_sync.sync_policy_from_renewal(r)
        self.assertEqual(result.policy_number, "EXIST1")   # returned existing, not a new one
        self.assertEqual(InsurancePolicy.objects.filter(
            client=self.client_rec, insurance_type=InsurancePolicy.TYPE_HEALTH).count(), 1)

    def test_other_renewal_does_not_create_a_policy(self):
        r = self._renewal(ptype=Renewal.PRODUCT_TYPE_OTHER, product_name="FD")
        self.assertIsNone(insurance_sync.sync_policy_from_renewal(r))
        self.assertFalse(InsurancePolicy.objects.filter(client=self.client_rec).exists())

    def test_client_health_life_lookup_excludes_motor(self):
        InsurancePolicy.objects.create(client=self.client_rec, policy_number="H1",
                                       insurer="A", insurance_type=InsurancePolicy.TYPE_HEALTH)
        InsurancePolicy.objects.create(client=self.client_rec, policy_number="M1",
                                       insurer="B", insurance_type=InsurancePolicy.TYPE_MOTOR)
        found = list(insurance_sync.client_health_life_policies(self.client_rec))
        self.assertEqual([p.policy_number for p in found], ["H1"])


class RenewalViewEndToEndTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _products()
        u = User.objects.create_user("rv_admin", password="pw")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=6003, name="Diya Sharma")
        cls.health = Product.objects.get(code="HEALTH_INS")

    def test_posting_a_health_renewal_backfills_the_tracker(self):
        from django.test import Client as TC
        from django.urls import reverse
        tc = TC(); tc.force_login(self.admin.user)
        resp = tc.post(reverse("clients:add_renewal"), {
            "client": self.client_rec.id, "employee": self.admin.id,
            "product_ref": self.health.id, "product_type": Renewal.PRODUCT_TYPE_HEALTH,
            "frequency": "yearly", "renewal_date": "2026-02-10",
            "premium_amount": "8000", "premium_collected_on": "2026-07-21",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(InsurancePolicy.objects.filter(
            client=self.client_rec, insurance_type=InsurancePolicy.TYPE_HEALTH).exists())

    def test_renewal_list_breaks_business_down_by_type(self):
        from django.test import Client as TC
        from django.urls import reverse
        # One health, one life renewal collected this month.
        from django.utils import timezone
        today = timezone.localdate()
        Renewal.objects.create(client=self.client_rec, employee=self.admin,
                               product_type=Renewal.PRODUCT_TYPE_HEALTH, frequency="yearly",
                               renewal_date=today, premium_amount=5000, premium_collected_on=today)
        Renewal.objects.create(client=self.client_rec, employee=self.admin,
                               product_type=Renewal.PRODUCT_TYPE_LIFE, frequency="yearly",
                               renewal_date=today, premium_amount=7000, premium_collected_on=today)
        tc = TC(); tc.force_login(self.admin.user)
        html = tc.get(reverse("clients:all_renewals")).content.decode()
        self.assertIn("Health Insurance", html)
        self.assertIn("Life Insurance", html)
        self.assertIn("5,000", html)   # health amount, Indian grouping
        self.assertIn("7,000", html)   # life amount

    def test_client_policies_endpoint_returns_health_life(self):
        from django.test import Client as TC
        from django.urls import reverse
        InsurancePolicy.objects.create(client=self.client_rec, policy_number="H9",
                                       insurer="Star Health", insurance_type=InsurancePolicy.TYPE_HEALTH)
        tc = TC(); tc.force_login(self.admin.user)
        data = tc.get(reverse("clients:client_policies_json", args=[self.client_rec.id])).json()
        self.assertEqual(len(data["policies"]), 1)
        self.assertEqual(data["policies"][0]["type"], "Health")
        self.assertEqual(data["policies"][0]["insurer"], "Star Health")


class PolicyNumberAndLinkingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _products()
        u = User.objects.create_user("pl_admin", password="pw")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=6100, name="Rahul Sharma")
        cls.health = Product.objects.get(code="HEALTH_INS")

    def test_sale_policy_number_becomes_the_tracker_number(self):
        sale = Sale(client=self.client_rec, employee=self.admin, product="Health Insurance",
                    amount=12000, policy_date=date(2026, 1, 5), date=date(2026, 3, 20),
                    policy_number="INS76123499")
        sales_service.finalize_new_sale(sale, self.admin.user, auto_approve=True)
        self.assertEqual(sale.policy.policy_number, "INS76123499")

    def test_insurance_sale_form_requires_policy_number(self):
        from clients.forms import AdminSaleForm
        data = {"client": self.client_rec.id, "employee": self.admin.id,
                "product": "Health Insurance", "amount": "12000", "policy_type": "fresh",
                "date": "2026-03-20", "policy_date": "2026-01-05", "policy_number": ""}
        form = AdminSaleForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("policy_number", form.errors)

    def test_renewal_links_to_selected_existing_policy(self):
        policy = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="EXIST9", insurer="Star Health",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        r = Renewal.objects.create(client=self.client_rec, employee=self.admin,
                                   product_ref=self.health, product_type=Renewal.PRODUCT_TYPE_HEALTH,
                                   frequency="yearly", renewal_date=date(2026, 2, 10),
                                   premium_amount=8000)
        insurance_sync.link_renewal_to_policy(r, selected_policy_id=policy.id)
        r.refresh_from_db()
        self.assertEqual(r.policy_id, policy.id)
        # No new policy created — it linked to the existing one.
        self.assertEqual(InsurancePolicy.objects.filter(client=self.client_rec).count(), 1)

    def test_renewal_with_new_number_creates_and_links_policy(self):
        r = Renewal.objects.create(client=self.client_rec, employee=self.admin,
                                   product_ref=self.health, product_type=Renewal.PRODUCT_TYPE_HEALTH,
                                   frequency="yearly", renewal_date=date(2026, 2, 10),
                                   premium_amount=8000)
        policy = insurance_sync.link_renewal_to_policy(r, new_policy_number="NEWPOL123")
        r.refresh_from_db()
        self.assertEqual(policy.policy_number, "NEWPOL123")
        self.assertEqual(r.policy_id, policy.id)
        self.assertEqual(policy.insurance_type, InsurancePolicy.TYPE_HEALTH)

    def test_selecting_a_policy_from_another_client_is_ignored(self):
        other = Client.objects.create(id=6101, name="Someone Else")
        other_policy = InsurancePolicy.objects.create(
            client=other, policy_number="OTHER1", insurer="X",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        r = Renewal.objects.create(client=self.client_rec, employee=self.admin,
                                   product_ref=self.health, product_type=Renewal.PRODUCT_TYPE_HEALTH,
                                   frequency="yearly", renewal_date=date(2026, 2, 10),
                                   premium_amount=8000)
        # Selecting another client's policy must not link it; falls through to new.
        policy = insurance_sync.link_renewal_to_policy(r, selected_policy_id=other_policy.id,
                                                       new_policy_number="MINE1")
        r.refresh_from_db()
        self.assertNotEqual(r.policy_id, other_policy.id)
        self.assertEqual(r.policy.client, self.client_rec)

    def test_posting_renewal_with_selected_policy_links_it(self):
        from django.test import Client as TC
        from django.urls import reverse
        policy = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="PICKME", insurer="HDFC",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        tc = TC(); tc.force_login(self.admin.user)
        tc.post(reverse("clients:add_renewal"), {
            "client": self.client_rec.id, "employee": self.admin.id,
            "product_ref": self.health.id, "frequency": "yearly",
            "renewal_date": "2026-02-10", "premium_amount": "8000",
            "premium_collected_on": "2026-07-21", "policy": policy.id,
        })
        r = Renewal.objects.filter(client=self.client_rec).latest("id")
        self.assertEqual(r.policy_id, policy.id)

    def test_policy_detail_shows_renewal_history(self):
        from django.test import Client as TC
        from django.urls import reverse
        policy = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="HIST1", insurer="ICICI",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        Renewal.objects.create(client=self.client_rec, employee=self.admin, policy=policy,
                               product_ref=self.health, product_type=Renewal.PRODUCT_TYPE_HEALTH,
                               frequency="yearly", renewal_date=date(2026, 2, 10),
                               premium_amount=8000, premium_collected_on=date(2026, 2, 11))
        tc = TC(); tc.force_login(self.admin.user)
        html = tc.get(reverse("clients:policy_detail", args=[policy.id])).content.decode()
        self.assertIn("Renewal history", html)
        self.assertIn("8,000", html)


class PolicyTypeMismatchTests(TestCase):
    """A renewal must never link to a policy of a different product line."""

    @classmethod
    def setUpTestData(cls):
        _products()
        u = User.objects.create_user("mm_admin", password="pw")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=6200, name="Aarav Malhotra")
        cls.life = Product.objects.get(code="LIFE_INS")

    def test_life_renewal_ignores_a_selected_health_policy(self):
        health = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="HEALTH1", insurer="Star",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        r = Renewal.objects.create(client=self.client_rec, employee=self.admin,
                                   product_ref=self.life, product_type=Renewal.PRODUCT_TYPE_LIFE,
                                   frequency="yearly", renewal_date=date(2026, 2, 10),
                                   premium_amount=9000)
        # User's stale selection is the Health policy — must NOT link to it.
        policy = insurance_sync.link_renewal_to_policy(
            r, selected_policy_id=health.id, new_policy_number="LIFE-NEW-1")
        r.refresh_from_db()
        self.assertNotEqual(r.policy_id, health.id)
        self.assertEqual(r.policy.insurance_type, InsurancePolicy.TYPE_LIFE)
        self.assertEqual(r.policy.policy_number, "LIFE-NEW-1")

    def test_life_renewal_links_to_the_life_policy_when_both_exist(self):
        InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="HEALTH1", insurer="Star",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        life_pol = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="LIFE1", insurer="LIC",
            insurance_type=InsurancePolicy.TYPE_LIFE)
        r = Renewal.objects.create(client=self.client_rec, employee=self.admin,
                                   product_ref=self.life, product_type=Renewal.PRODUCT_TYPE_LIFE,
                                   frequency="yearly", renewal_date=date(2026, 2, 10),
                                   premium_amount=9000)
        insurance_sync.link_renewal_to_policy(r, selected_policy_id=life_pol.id)
        r.refresh_from_db()
        self.assertEqual(r.policy_id, life_pol.id)


class AppRenewalPolicyLinkTests(TestCase):
    """Renewals entered on the phone must reach the tracker the same way the
    web form does. They used to be orphan rows: `link_renewal_to_policy` was
    only ever called from renewal_views.py."""

    @classmethod
    def setUpTestData(cls):
        _products()
        u = User.objects.create_user("app_ren_admin", password="pw")
        cls.admin = Employee.objects.create(user=u, role="admin", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=6200, name="Anjali Deshmukh")
        cls.health = Product.objects.get(code="HEALTH_INS")

    def _post(self, **body):
        import json
        from django.test import Client as TC
        from django.urls import reverse
        tc = TC()
        tc.force_login(self.admin.user)
        payload = {
            "client_id": self.client_rec.id, "product_id": self.health.id,
            "premium_amount": "8000", "renewal_date": "2026-02-10",
            "frequency": "yearly",
        }
        payload.update(body)
        return tc.post(
            reverse("clients:app_renewal_create"),
            data=json.dumps(payload), content_type="application/json",
        )

    def test_app_renewal_links_to_the_ticked_policy(self):
        policy = InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="EXIST42", insurer="Star Health",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        resp = self._post(policy_id=policy.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["policy_id"], policy.id)
        self.assertEqual(Renewal.objects.get(pk=resp.json()["id"]).policy_id, policy.id)
        self.assertEqual(InsurancePolicy.objects.filter(client=self.client_rec).count(), 1)

    def test_app_renewal_with_a_new_number_backfills_the_old_book(self):
        resp = self._post(policy_number="OLDBOOK77")
        self.assertEqual(resp.status_code, 200, resp.content)
        renewal = Renewal.objects.get(pk=resp.json()["id"])
        self.assertIsNotNone(renewal.policy_id)
        self.assertEqual(renewal.policy.policy_number, "OLDBOOK77")
        self.assertEqual(renewal.policy.insurance_type, InsurancePolicy.TYPE_HEALTH)

    def test_app_renewal_without_any_policy_choice_still_creates_one(self):
        # Matches the web fallback: an insurance renewal always ends up on the
        # tracker, with a placeholder number to be corrected later.
        resp = self._post()
        renewal = Renewal.objects.get(pk=resp.json()["id"])
        self.assertIsNotNone(renewal.policy_id)

    def test_app_renewal_policies_endpoint_lists_client_policies(self):
        from django.test import Client as TC
        from django.urls import reverse
        InsurancePolicy.objects.create(
            client=self.client_rec, policy_number="LIST1",
            insurance_type=InsurancePolicy.TYPE_HEALTH)
        tc = TC()
        tc.force_login(self.admin.user)
        data = tc.get(
            reverse("clients:client_policies_json", args=[self.client_rec.id])
        ).json()
        self.assertTrue(any(p["number"] == "LIST1" for p in data["policies"]))

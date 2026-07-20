"""Households: combined AUM, wealth banding, and the list/detail screens."""
from django.contrib.auth.models import User
from django.test import Client as TC, TestCase
from django.urls import reverse

from clients.models import Client, Employee, Family
from clients.templatetags.custom_filters import mask_email, mask_pan, mask_phone


class FamilyAumTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("famadmin", password="pw")
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)

    def _family(self, code, *amounts, pms=0):
        fam = Family.objects.create(name=f"House {code}", code=code)
        for i, amt in enumerate(amounts):
            Client.objects.create(id=int(code[1:]) * 100 + i, name=f"M{i} {code}",
                                  lumsum_investment=amt, pms_amount=pms, family=fam)
        return fam

    def test_aum_sums_lumpsum_and_pms_across_members(self):
        fam = self._family("A001", 1_000_000, 500_000, pms=250_000)
        # two members: (10L + 2.5L) + (5L + 2.5L)
        self.assertEqual(fam.aum, 2_000_000)

    def test_empty_household_is_zero_not_error(self):
        fam = Family.objects.create(name="Empty", code="A999")
        self.assertEqual(fam.aum, 0)
        self.assertEqual(fam.category, "Middle (Below 50 Lakh)")

    def test_bands_at_their_boundaries(self):
        cases = [
            (60_000_000, "Super HNI (Above 5 Cr)"),
            (50_000_000, "Super HNI (Above 5 Cr)"),   # exactly 5 Cr
            (49_999_999, "HNI (1 Cr to 5 Cr)"),
            (10_000_000, "HNI (1 Cr to 5 Cr)"),       # exactly 1 Cr
            (9_999_999, "Upper Middle (50 L to 1 Cr)"),
            (5_000_000, "Upper Middle (50 L to 1 Cr)"),  # exactly 50 L
            (4_999_999, "Middle (Below 50 Lakh)"),
            (0, "Middle (Below 50 Lakh)"),
        ]
        for i, (amount, expected) in enumerate(cases):
            fam = self._family(f"B{i:03d}", amount)
            self.assertEqual(fam.category, expected, f"₹{amount}")


class FamilyScreenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("famuser", password="pw")
        Employee.objects.create(user=cls.u, role="admin", salary=0, active=True)
        cls.fam = Family.objects.create(name="Parekh Family", code="A001")
        Client.objects.create(id=7001, name="Ravi Parekh", lumsum_investment=20_000_000,
                              phone="9812345678", family=cls.fam)

    def _get(self, url):
        tc = TC(); tc.force_login(self.u)
        r = tc.get(url)
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_list_shows_household_with_band(self):
        html = self._get(reverse("clients:family_list"))
        self.assertIn("Parekh Family", html)
        self.assertIn("HNI (1 Cr to 5 Cr)", html)
        self.assertIn("2,00,00,000", html)

    def test_band_filter_narrows_the_list(self):
        tc = TC(); tc.force_login(self.u)
        hit = tc.get(reverse("clients:family_list"), {"band": "HNI (1 Cr to 5 Cr)"})
        self.assertIn("Parekh Family", hit.content.decode())
        miss = tc.get(reverse("clients:family_list"), {"band": "Middle (Below 50 Lakh)"})
        self.assertNotIn("Parekh Family", miss.content.decode())

    def test_detail_lists_members_with_masked_phone(self):
        html = self._get(reverse("clients:family_detail", args=[self.fam.id]))
        self.assertIn("Ravi Parekh", html)
        self.assertIn("98******78", html)
        self.assertNotIn("9812345678", html)   # raw number must not leak


class MaskingTests(TestCase):
    def test_masks_keep_shape_and_hide_the_middle(self):
        self.assertEqual(mask_phone("9812345678"), "98******78")
        self.assertEqual(mask_email("rahul.sharma@gmail.com"), "rah***a@gmail.com")
        self.assertEqual(mask_pan("ABCDE1234F"), "ABCD****4F")

    def test_masks_survive_junk_input(self):
        for f in (mask_phone, mask_email, mask_pan):
            self.assertEqual(f(None), "—")
            self.assertEqual(f(""), "—")
        self.assertEqual(mask_phone("+91 98123-45678"), "91********78")  # 12 digits -> 8 stars
        self.assertEqual(mask_email("no-at-sign"), "no-at-sign")

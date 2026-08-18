"""A phone number is a matching key, so it is normalised on the way in.

A spreadsheet import stored 2,168 client numbers as "9423440791.0". The
damage was not a failed match — every matcher strips non-digits and takes the
last ten, so that number read as 4234407910, a *different* person. These
tests pin the normaliser, the model save paths that can never be bypassed,
and the display-time name resolution that fixes rows already written.

Run: .venv/bin/python manage.py test clients.test.test_phone_normalisation
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from io import StringIO

from clients.models import CallFollowUp, Client, Employee, Lead
from clients.services import calendar_feed, calls as calls_service
from clients.utils.phone_utils import clean_phone, digits10, normalize_phone


class NormaliserTests(TestCase):
    def test_the_excel_float_tail_is_removed(self):
        self.assertEqual(clean_phone("9423440791.0"), "9423440791")

    def test_a_clean_number_is_untouched(self):
        for raw in ("9423440791", "+917745069117", "07745069117"):
            self.assertEqual(clean_phone(raw), raw)

    def test_surrounding_whitespace_goes(self):
        self.assertEqual(clean_phone("  9423440791  "), "9423440791")

    def test_a_non_float_decimal_is_left_alone(self):
        # Only ".0" is the known import artifact; anything else is real input
        # and must not be guessed at.
        self.assertEqual(clean_phone("9423440791.5"), "9423440791.5")

    def test_blank_input_is_safe(self):
        for raw in (None, "", "   "):
            self.assertEqual(clean_phone(raw), "")
            self.assertEqual(digits10(raw), "")

    def test_the_float_tail_used_to_shift_the_matching_window(self):
        # This is the whole bug: naive stripping gives 4234407910.
        self.assertEqual(digits10("9423440791.0"), "9423440791")
        self.assertEqual(digits10("9423440791.0"), digits10("+919423440791"))

    def test_whatsapp_links_normalise_from_a_float_row_too(self):
        e164, wa = normalize_phone("9423440791.0")
        self.assertEqual(wa, "919423440791")


class ModelSaveTests(TestCase):
    """The model is the only layer every write path goes through."""

    def test_client_save_normalises(self):
        c = Client.objects.create(name="FLOATY", phone="9423440791.0")
        c.refresh_from_db()
        self.assertEqual(c.phone, "9423440791")

    def test_lead_save_normalises(self):
        user = User.objects.create_user("ph_emp", password="x")
        emp = Employee.objects.create(user=user, role="employee", salary=0, active=True)
        lead = Lead.objects.create(customer_name="Floaty Lead", phone="7038204121.0",
                                   assigned_to=emp)
        lead.refresh_from_db()
        self.assertEqual(lead.phone, "7038204121")

    def test_a_client_without_a_phone_still_saves(self):
        c = Client.objects.create(name="NO PHONE")
        self.assertIn(c.phone, (None, ""))


class CallerNameTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user("ph_caller", password="x")
        cls.emp = Employee.objects.create(user=user, role="employee", salary=0, active=True)
        cls.client_row = Client.objects.create(name="ASHA PATIL", phone="9423440791")
        cls.lead = Lead.objects.create(customer_name="Prem Borse", phone="9021078186",
                                       assigned_to=cls.emp)

    def test_a_client_is_found_however_the_number_was_dialled(self):
        for dialled in ("9423440791", "+919423440791", "09423440791"):
            found = calls_service.caller_names([dialled])
            self.assertEqual(found[digits10(dialled)][0], "ASHA PATIL", dialled)
            self.assertEqual(found[digits10(dialled)][1], "client")

    def test_a_lead_is_found_too(self):
        found = calls_service.caller_names(["+919021078186"])
        self.assertEqual(found["9021078186"][:2], ("Prem Borse", "lead"))

    def test_a_client_beats_a_lead_on_the_same_number(self):
        Lead.objects.create(customer_name="Same Number", phone="9423440791",
                            assigned_to=self.emp)
        self.assertEqual(calls_service.caller_names(["9423440791"])["9423440791"][1], "client")

    def test_a_lost_lead_is_not_offered_as_a_name(self):
        Lead.objects.filter(pk=self.lead.pk).update(is_discarded=True)
        self.assertEqual(calls_service.caller_names(["9021078186"]), {})

    def test_an_unknown_number_resolves_to_nothing(self):
        self.assertEqual(calls_service.caller_names(["9999900000"]), {})

    def test_no_numbers_means_no_queries(self):
        with self.assertNumQueries(0):
            self.assertEqual(calls_service.caller_names([]), {})

    def test_the_whole_screen_costs_two_queries(self):
        phones = [f"90000000{i:02d}" for i in range(40)]
        with self.assertNumQueries(2):
            calls_service.caller_names(phones)


class AgendaNameTests(TestCase):
    """Rows already written with no client link still show a name."""

    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user("ph_agenda", password="x")
        cls.emp = Employee.objects.create(user=user, role="employee", salary=0, active=True)
        now = timezone.now()
        cls.client_row = Client.objects.create(name="ASHA PATIL", phone="9423440791")
        cls.lead = Lead.objects.create(customer_name="Prem Borse", phone="9021078186",
                                       assigned_to=cls.emp)
        # Both created without a client link, exactly as production rows were.
        cls.to_client = CallFollowUp.objects.create(
            employee=cls.emp, phone="+919423440791", scheduled_at=now + timedelta(hours=1))
        cls.to_lead = CallFollowUp.objects.create(
            employee=cls.emp, phone="+919021078186", scheduled_at=now + timedelta(hours=2))
        cls.to_nobody = CallFollowUp.objects.create(
            employee=cls.emp, phone="+919999900000", scheduled_at=now + timedelta(hours=3))

    def _titles(self):
        return {it["key"]: it for it in calendar_feed.feed_items(self.emp)}

    def test_a_client_shows_by_name_even_though_the_row_is_unlinked(self):
        item = self._titles()[f"call-{self.to_client.id}"]
        self.assertEqual(item["title"], "Call ASHA PATIL")
        self.assertIn(f"/{self.client_row.id}/", item["url"])

    def test_a_lead_shows_by_name_and_links_to_the_lead(self):
        item = self._titles()[f"call-{self.to_lead.id}"]
        self.assertEqual(item["title"], "Call Prem Borse")
        self.assertIn(f"/leads/{self.lead.id}/", item["url"])

    def test_a_genuinely_unknown_number_still_shows_the_number(self):
        # There is no name to invent — this is correct, not a failure.
        self.assertEqual(self._titles()[f"call-{self.to_nobody.id}"]["title"],
                         "Call +919999900000")

    def test_a_linked_client_is_not_re_resolved(self):
        self.to_nobody.client = self.client_row
        self.to_nobody.save(update_fields=["client"])
        self.assertEqual(self._titles()[f"call-{self.to_nobody.id}"]["title"],
                         "Call ASHA PATIL")


class FixCommandTests(TestCase):
    def setUp(self):
        # Bypass save() so a corrupted row can exist to be repaired.
        self.c = Client.objects.create(name="FLOATY", phone="9423440791")
        Client.objects.filter(pk=self.c.pk).update(phone="9423440791.0")

    def _run(self, *args):
        out = StringIO()
        call_command("fix_phone_floats", *args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_reports_but_writes_nothing(self):
        output = self._run()
        self.assertIn("1 phone number(s) to repair", output)
        self.assertIn("Re-run with --apply", output)
        self.c.refresh_from_db()
        self.assertEqual(self.c.phone, "9423440791.0", "dry run must not write")

    def test_apply_repairs_the_row(self):
        self._run("--apply")
        self.c.refresh_from_db()
        self.assertEqual(self.c.phone, "9423440791")

    def test_a_second_run_is_a_no_op(self):
        self._run("--apply")
        self.assertIn("Nothing to do", self._run())

    def test_a_clean_number_is_never_touched(self):
        good = Client.objects.create(name="GOOD", phone="+917745069117")
        self._run("--apply")
        good.refresh_from_db()
        self.assertEqual(good.phone, "+917745069117")


class RingingAlarmNameTests(TestCase):
    """The app's alarm reads `client` off /api/app/followups/ and falls back to
    the raw number (FollowupAlarmScheduler.syncFromPending). Fixing the payload
    fixes every phone in the field without an APK release."""

    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user("ph_ring", password="x")
        cls.emp = Employee.objects.create(user=user, role="employee", salary=0, active=True)
        now = timezone.now()
        cls.client_row = Client.objects.create(name="ASHA PATIL", phone="9423440791")
        cls.lead = Lead.objects.create(customer_name="Prem Borse", phone="9021078186",
                                       assigned_to=cls.emp)
        # Unlinked, exactly as the production rows are.
        cls.to_client = CallFollowUp.objects.create(
            employee=cls.emp, phone="+919423440791", scheduled_at=now + timedelta(hours=1))
        cls.to_lead = CallFollowUp.objects.create(
            employee=cls.emp, phone="+919021078186", scheduled_at=now + timedelta(hours=2))
        cls.to_nobody = CallFollowUp.objects.create(
            employee=cls.emp, phone="+919999900000", scheduled_at=now + timedelta(hours=3))

    def _rows(self):
        from django.test import Client as TestClient
        from django.urls import reverse
        tc = TestClient(); tc.force_login(self.emp.user)
        payload = tc.get(reverse("clients:app_followups")).json()
        return {r["id"]: r for r in payload["pending"]}

    def test_the_alarm_gets_a_client_name_for_an_unlinked_row(self):
        self.assertEqual(self._rows()[self.to_client.id]["client"], "ASHA PATIL")

    def test_the_alarm_gets_a_lead_name_too(self):
        self.assertEqual(self._rows()[self.to_lead.id]["client"], "Prem Borse")

    def test_an_unknown_number_leaves_client_blank_so_the_app_shows_the_number(self):
        self.assertEqual(self._rows()[self.to_nobody.id]["client"], "")

    def test_resolving_names_does_not_cost_a_query_per_row(self):
        """The count must not grow with the list — a magic number would just
        rot, so compare a small screen against a large one."""
        from django.test import Client as TestClient
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from django.urls import reverse

        tc = TestClient(); tc.force_login(self.emp.user)
        now = timezone.now()

        def count_queries():
            tc.get(reverse("clients:app_followups"))          # warm any caches
            with CaptureQueriesContext(connection) as ctx:
                tc.get(reverse("clients:app_followups"))
            return len(ctx)

        small = count_queries()
        for i in range(40):
            CallFollowUp.objects.create(employee=self.emp, phone=f"90000000{i:02d}",
                                        scheduled_at=now + timedelta(hours=4))
        large = count_queries()
        self.assertEqual(small, large,
                         f"query count grew with rows ({small} -> {large}) — N+1")

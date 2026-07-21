"""Employee profile data, tenure maths, and milestone generation."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import Employee, EmployeeMilestone
from clients.services import people


def _emp(username, **kw):
    u = User.objects.create_user(username, password="pw")
    return Employee.objects.create(user=u, role="employee", salary=0, active=True, **kw)


class TenureTests(TestCase):
    def test_tenure_counts_whole_months_only(self):
        today = timezone.localdate()
        e = _emp("t1", joining_date=today - timedelta(days=400))
        # 400 days is 13 months and a bit.
        self.assertEqual(e.tenure_months, 13)

    def test_tenure_does_not_round_up_a_partial_month(self):
        today = timezone.localdate()
        # Joined last month but on a later day-of-month → not a full month yet.
        joined = (today.replace(day=1) - timedelta(days=1)).replace(day=28)
        e = _emp("t2", joining_date=joined)
        if today.day < 28:
            self.assertEqual(e.tenure_months, 0)

    def test_unknown_joining_date_is_zero_not_error(self):
        e = _emp("t3")
        self.assertEqual(e.tenure_months, 0)
        self.assertEqual(e.tenure_display, "—")

    def test_humanise_months(self):
        cases = [(0, "—"), (5, "5m"), (12, "1y"), (14, "1y 2m"), (36, "3y")]
        for months, expected in cases:
            self.assertEqual(Employee.humanise_months(months), expected, months)


class NameTests(TestCase):
    def test_full_name_prefers_crm_fields_then_user_then_login(self):
        e = _emp("n1", first_name="Mansi", middle_name="R", last_name="Upasane")
        self.assertEqual(e.full_name, "Mansi R Upasane")

        e2 = _emp("n2")
        e2.user.first_name, e2.user.last_name = "Fall", "Back"
        e2.user.save()
        self.assertEqual(e2.full_name, "Fall Back")

        e3 = _emp("n3")
        self.assertEqual(e3.full_name, "n3")


class CompletenessTests(TestCase):
    def test_empty_profile_is_zero_percent_and_lists_every_gap(self):
        e = _emp("c1")
        self.assertEqual(e.profile_completeness, 0)
        self.assertFalse(e.profile_is_complete)
        self.assertEqual(len(e.missing_fields()), len(Employee.SELF_SERVICE_FIELDS))

    def test_full_profile_is_complete(self):
        e = _emp("c2", first_name="A", last_name="B", date_of_birth=date(1995, 5, 5),
                 phone="9812345678", personal_email="a@b.com", address="Somewhere",
                 qualification="B.Com", emergency_contact_name="C",
                 emergency_contact_phone="9800000000")
        self.assertEqual(e.profile_completeness, 100)
        self.assertTrue(e.profile_is_complete)

    def test_admin_fields_are_not_the_employees_problem(self):
        e = _emp("c3", first_name="A", last_name="B", date_of_birth=date(1995, 5, 5),
                 phone="9", personal_email="a@b.com", address="X", qualification="Y",
                 emergency_contact_name="C", emergency_contact_phone="9")
        # Complete for the employee, but the admin still owes joining date etc.
        self.assertTrue(e.profile_is_complete)
        self.assertEqual([f for f, _ in e.missing_fields(include_admin=True)],
                         ["joining_date", "position", "domain"])


class MilestoneGenerationTests(TestCase):
    def test_birthday_and_anniversary_are_generated(self):
        today = timezone.localdate()
        e = _emp("m1",
                 date_of_birth=date(1990, today.month, today.day),
                 joining_date=date(today.year - 3, today.month, today.day))
        people.generate_for(e, today)
        kinds = set(e.milestones.values_list("kind", flat=True))
        self.assertIn(EmployeeMilestone.Kind.BIRTHDAY, kinds)
        # 3 years is a long-service year.
        self.assertIn(EmployeeMilestone.Kind.LONG_SERVICE, kinds)

    def test_non_landmark_anniversary_is_a_plain_one(self):
        today = timezone.localdate()
        e = _emp("m2", joining_date=date(today.year - 2, today.month, today.day))
        people.generate_for(e, today)
        self.assertTrue(e.milestones.filter(
            kind=EmployeeMilestone.Kind.WORK_ANNIVERSARY, years=2).exists())

    def test_generation_is_idempotent(self):
        today = timezone.localdate()
        e = _emp("m3", date_of_birth=date(1990, today.month, today.day),
                 joining_date=date(today.year - 1, today.month, today.day))
        people.generate_for(e, today)
        first = e.milestones.count()
        people.generate_for(e, today)
        self.assertEqual(e.milestones.count(), first)

    def test_29_february_is_skipped_not_shifted(self):
        """A 29 Feb birthday must not silently become the 28th or 1st."""
        e = _emp("m4", date_of_birth=date(1992, 2, 29))
        people.generate_for(e, date(2027, 1, 1))     # 2027 is not a leap year
        days = [m.occurs_on for m in e.milestones.all()]
        self.assertNotIn(date(2027, 2, 28), days)
        self.assertNotIn(date(2027, 3, 1), days)

    def test_no_dates_means_no_milestones(self):
        e = _emp("m5")
        self.assertEqual(people.generate_for(e), [])


class CelebrationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("celebadmin", password="pw")
        self.emp = _emp("m6", first_name="Mansi")
        today = timezone.localdate()
        self.m = EmployeeMilestone.objects.create(
            employee=self.emp, kind=EmployeeMilestone.Kind.BIRTHDAY,
            occurs_on=today, title="Mansi's birthday")

    def test_celebrating_notifies_the_person(self):
        from clients.models import Notification
        people.celebrate(self.m, self.admin, "Cake at 4pm!")
        self.m.refresh_from_db()
        self.assertTrue(self.m.is_celebrated)
        note = Notification.objects.filter(recipient=self.emp.user).first()
        self.assertIsNotNone(note, "the employee must actually hear about it")
        self.assertIn("Cake at 4pm!", note.body)

    def test_missed_lists_uncelebrated_past_milestones_within_grace(self):
        today = timezone.localdate()
        self.m.occurs_on = today - timedelta(days=3)
        self.m.save()
        self.assertIn(self.m, list(people.missed()))
        # Celebrating removes it from the nudge list.
        people.celebrate(self.m, self.admin)
        self.assertNotIn(self.m, list(people.missed()))

    def test_old_misses_fall_out_of_the_nudge_list(self):
        today = timezone.localdate()
        self.m.occurs_on = today - timedelta(days=400)
        self.m.save()
        self.assertNotIn(self.m, list(people.missed()),
                         "an old backlog should not nag forever")

    def test_upcoming_window(self):
        today = timezone.localdate()
        self.m.occurs_on = today + timedelta(days=5)
        self.m.save()
        self.assertIn(self.m, list(people.upcoming()))
        self.m.occurs_on = today + timedelta(days=90)
        self.m.save()
        self.assertNotIn(self.m, list(people.upcoming()))


class MilestoneCronTests(TestCase):
    def test_cron_generates_and_notifies_admins_not_the_person(self):
        from io import StringIO
        from django.core.management import call_command
        from clients.models import Notification

        admin_user = User.objects.create_user("cronadmin", password="pw")
        Employee.objects.create(user=admin_user, role="admin", salary=0, active=True)
        today = timezone.localdate()
        emp = _emp("cronemp", first_name="Mansi",
                   date_of_birth=date(1990, today.month, today.day))

        call_command("employee_milestones", stdout=StringIO())
        self.assertTrue(emp.milestones.exists())
        # The admin is told; the birthday person is not asked to celebrate
        # their own birthday.
        self.assertTrue(Notification.objects.filter(recipient=admin_user).exists())
        self.assertFalse(Notification.objects.filter(recipient=emp.user).exists())

    def test_admin_is_not_asked_to_celebrate_themselves(self):
        from io import StringIO
        from django.core.management import call_command
        from clients.models import Notification

        today = timezone.localdate()
        u = User.objects.create_user("soloadmin", password="pw")
        Employee.objects.create(user=u, role="admin", salary=0, active=True,
                                first_name="Solo",
                                date_of_birth=date(1985, today.month, today.day))
        call_command("employee_milestones", stdout=StringIO())
        self.assertFalse(Notification.objects.filter(recipient=u).exists())

    def test_dry_run_notifies_nobody(self):
        from io import StringIO
        from django.core.management import call_command
        from clients.models import Notification

        admin_user = User.objects.create_user("dryadmin", password="pw")
        Employee.objects.create(user=admin_user, role="admin", salary=0, active=True)
        today = timezone.localdate()
        _emp("dryemp", date_of_birth=date(1990, today.month, today.day))
        call_command("employee_milestones", "--dry-run", stdout=StringIO())
        self.assertEqual(Notification.objects.count(), 0)


class TeamEditRoundTripTests(TestCase):
    """The admin edit form must persist the new employment fields."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("teamadmin", password="pw")
        Employee.objects.create(user=cls.admin_user, role="admin", salary=0, active=True)
        cls.emp = _emp("editme", joining_date=date(2020, 4, 1))

    def _post(self, **overrides):
        from django.test import Client as TC
        data = {
            "first_name": "Mansi", "last_name": "Upasane", "middle_name": "R",
            "role": "employee", "salary": "25000",
            "position": "Relationship Manager", "domain": "sales",
            "joining_date": "2021-06-15", "date_of_birth": "1996-03-09",
            "marital_status": "married", "phone": "9812345678",
            "personal_email": "m@example.com", "qualification": "B.Com",
            "skills": "MFD, NISM-VA", "emergency_contact_name": "Ravi",
            "emergency_contact_phone": "9800000000",
            "address": "Pune",
        }
        data.update(overrides)
        tc = TC(); tc.force_login(self.admin_user)
        return tc.post(reverse("clients:team_edit", args=[self.emp.id]), data)

    def test_all_new_fields_persist(self):
        self._post()
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.full_name, "Mansi R Upasane")
        self.assertEqual(self.emp.position, "Relationship Manager")
        self.assertEqual(self.emp.domain, "sales")
        self.assertEqual(self.emp.joining_date, date(2021, 6, 15))
        self.assertEqual(self.emp.date_of_birth, date(1996, 3, 9))
        self.assertEqual(self.emp.marital_status, "married")
        self.assertEqual(self.emp.skill_list(), ["MFD", "NISM-VA"])
        self.assertEqual(self.emp.emergency_contact_name, "Ravi")

    def test_blank_date_does_not_wipe_a_known_one(self):
        """A half-filled resubmit must not erase the joining date."""
        self._post()
        self._post(joining_date="", date_of_birth="")
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.joining_date, date(2021, 6, 15))
        self.assertEqual(self.emp.date_of_birth, date(1996, 3, 9))

    def test_saving_dates_generates_milestones(self):
        self._post()
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.milestones.exists(),
                        "entering a birthday should create something to celebrate")

    def test_employee_cannot_reach_the_admin_edit(self):
        from django.test import Client as TC
        tc = TC(); tc.force_login(self.emp.user)
        r = tc.get(reverse("clients:team_edit", args=[self.emp.id]))
        self.assertEqual(r.status_code, 403)


class MyProfileTests(TestCase):
    def setUp(self):
        self.emp = _emp("selfserve")

    def test_employee_can_fill_their_own_profile(self):
        from django.test import Client as TC
        tc = TC(); tc.force_login(self.emp.user)
        r = tc.post(reverse("clients:my_profile"), {
            "first_name": "Mansi", "last_name": "Upasane",
            "date_of_birth": "1996-03-09", "phone": "9812345678",
            "personal_email": "m@example.com", "address": "Pune",
            "qualification": "B.Com", "emergency_contact_name": "Ravi",
            "emergency_contact_phone": "9800000000",
            "middle_name": "", "marital_status": "single", "skills": "",
        })
        self.assertEqual(r.status_code, 302)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.profile_completeness, 100)
        self.assertIsNotNone(self.emp.profile_updated_at)

    def test_name_mirrors_onto_user_so_greetings_use_it(self):
        """Empty profile → greeted by login id; filled → greeted by name."""
        self.assertEqual(self.emp.user.get_full_name(), "")

        self.emp.first_name, self.emp.last_name = "Mansi", "Upasane"
        self.emp.save()
        self.emp.user.refresh_from_db()
        self.assertEqual(self.emp.user.get_full_name(), "Mansi Upasane")

    def test_employee_can_change_their_login_id(self):
        from django.test import Client as TC
        tc = TC(); tc.force_login(self.emp.user)
        r = tc.post(reverse("clients:my_profile"),
                    {"form": "login_id", "username": "mansi.u"})
        self.assertEqual(r.status_code, 302)
        self.emp.user.refresh_from_db()
        self.assertEqual(self.emp.user.username, "mansi.u")

    def test_login_id_must_stay_unique(self):
        from django.test import Client as TC
        other = _emp("taken")
        tc = TC(); tc.force_login(self.emp.user)
        r = tc.post(reverse("clients:my_profile"),
                    {"form": "login_id", "username": other.user.username})
        self.assertEqual(r.status_code, 200)  # re-rendered with the error
        self.emp.user.refresh_from_db()
        self.assertEqual(self.emp.user.username, "selfserve")

    def test_employee_can_change_their_password_and_stays_logged_in(self):
        from django.test import Client as TC
        self.emp.user.set_password("oldpass123")
        self.emp.user.save()
        tc = TC()
        self.assertTrue(tc.login(username="selfserve", password="oldpass123"))
        r = tc.post(reverse("clients:my_profile"), {
            "form": "password", "old_password": "oldpass123",
            "new_password1": "Str0ng!Passw0rd", "new_password2": "Str0ng!Passw0rd",
        })
        self.assertEqual(r.status_code, 302)
        self.emp.user.refresh_from_db()
        self.assertTrue(self.emp.user.check_password("Str0ng!Passw0rd"))
        # Session survived the change — no surprise logout.
        self.assertEqual(tc.get(reverse("clients:my_profile")).status_code, 200)

    def test_wrong_old_password_is_rejected(self):
        from django.test import Client as TC
        self.emp.user.set_password("oldpass123")
        self.emp.user.save()
        tc = TC(); tc.force_login(self.emp.user)
        tc.post(reverse("clients:my_profile"), {
            "form": "password", "old_password": "nope",
            "new_password1": "Str0ng!Passw0rd", "new_password2": "Str0ng!Passw0rd",
        })
        self.emp.user.refresh_from_db()
        self.assertTrue(self.emp.user.check_password("oldpass123"))

    def test_profile_form_cannot_change_salary_or_role(self):
        """Self-service must not be a path to a pay rise."""
        from clients.forms import MyProfileForm
        for forbidden in ("salary", "role", "employee_number", "joining_date", "position"):
            self.assertNotIn(forbidden, MyProfileForm.Meta.fields)

"""Date of birth on a client, and the alerts it drives: the birthday call
every year, and the retirement-planning task at 40.

Date of birth became mandatory for new clients in Sep 2026 — on the web form
AND the app API, because guarding only the browser would let every
phone-added client skip it. Old clients keep their blank and are filled in on
the KYC screen. The client's age is the point of collecting it: at 40, the
mapped employee gets a ringing task to start retirement planning.

Run: .venv/bin/python manage.py test clients.test.test_client_dob_retirement -v 2
"""
import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client as DjangoClient, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.forms import ClientForm
from clients.models import Client, Employee, Task
from clients.services import client_alerts


def _dob_for_age(age, today=None):
    """A birth date that makes someone exactly `age` today."""
    today = today or timezone.localdate()
    try:
        return today.replace(year=today.year - age)
    except ValueError:                       # today is 29 Feb
        return today.replace(year=today.year - age, day=28)


class ClientAgeTests(TestCase):
    def test_age_counts_whole_years_and_respects_the_birthday(self):
        today = timezone.localdate()
        c = Client(name="A", date_of_birth=_dob_for_age(40))
        self.assertEqual(c.age, 40)
        # One day short of the birthday is still 39 — the alert must not fire early.
        c.date_of_birth = _dob_for_age(40) + timedelta(days=1)
        self.assertEqual(c.age, 39)
        self.assertLessEqual(c.date_of_birth, today)

    def test_a_client_with_no_date_of_birth_has_no_age(self):
        self.assertIsNone(Client(name="Old Import").age,
                          "a missing date of birth must never read as age 0")


class ClientFormDobTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.existing = Client.objects.create(id=7101, name="LEGACY CLIENT")

    def _add_data(self, **kw):
        data = {"name": "New Client", "phone": "9876500011",
                "email": "new@example.com", "pan": "ABCDE1234F",
                "date_of_birth": _dob_for_age(35).isoformat(),
                "lumsum_investment": "0"}
        data.update(kw)
        return data

    def test_a_new_client_needs_a_date_of_birth(self):
        form = ClientForm(data=self._add_data(date_of_birth=""))
        self.assertFalse(form.is_valid())
        self.assertIn("date_of_birth", form.errors)

    def test_a_new_client_with_one_is_accepted(self):
        self.assertTrue(ClientForm(data=self._add_data()).is_valid())

    def test_an_old_client_can_still_be_edited_without_one(self):
        """The whole reason the column stays nullable — 2,900 imported clients
        must not become uneditable the day the rule lands."""
        form = ClientForm(data=self._add_data(date_of_birth=""), instance=self.existing)
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_future_date_of_birth_is_refused(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        form = ClientForm(data=self._add_data(date_of_birth=tomorrow))
        self.assertFalse(form.is_valid())

    def test_a_mistyped_century_is_refused(self):
        form = ClientForm(data=self._add_data(date_of_birth="1826-04-02"))
        self.assertFalse(form.is_valid())


class AppClientCreateDobTests(TestCase):
    """The phone is a real add-client path — a rule enforced only in the
    browser is not enforced."""

    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("dob_emp", password="pw")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)

    def _post(self, **kw):
        body = {"name": "Phone Client", "phone": "9876500022", "pan": "ABCDE1234F",
                "date_of_birth": _dob_for_age(30).isoformat()}
        body.update(kw)
        c = DjangoClient()
        c.force_login(self.emp.user)
        return c.post(reverse("clients:app_client_create"),
                      data=json.dumps(body), content_type="application/json")

    def test_the_app_refuses_a_client_with_no_date_of_birth(self):
        resp = self._post(date_of_birth="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Date of birth", resp.json()["error"])
        self.assertFalse(Client.objects.filter(name="Phone Client").exists())

    def test_the_app_accepts_one_with_a_date_of_birth(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Client.objects.get(name="Phone Client").age, 30)

    def test_the_app_refuses_a_future_date_of_birth(self):
        resp = self._post(date_of_birth=(timezone.localdate() + timedelta(days=1)).isoformat())
        self.assertEqual(resp.status_code, 400)


class KycDobFillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        au = User.objects.create_user("dob_admin", password="pw")
        cls.admin = Employee.objects.create(user=au, role="admin", salary=0, active=True)
        eu = User.objects.create_user("dob_emp2", password="pw")
        cls.emp = Employee.objects.create(user=eu, role="employee", salary=0, active=True)
        ou = User.objects.create_user("dob_other", password="pw")
        cls.other = Employee.objects.create(user=ou, role="employee", salary=0, active=True)
        cls.client_rec = Client.objects.create(id=7201, name="NO DOB", mapped_to=cls.emp)

    def _as(self, emp):
        c = DjangoClient()
        c.force_login(emp.user)
        return c

    def test_the_kyc_page_lists_clients_with_no_date_of_birth(self):
        html = self._as(self.admin).get(reverse("clients:client_kyc_issues")).content.decode()
        self.assertIn("Clients missing date of birth", html)
        self.assertIn("NO DOB", html)

    def test_a_date_of_birth_can_be_filled_in_from_the_kyc_page(self):
        self._as(self.emp).post(
            reverse("clients:client_kyc_update_dob", args=[self.client_rec.id]),
            {"date_of_birth": "1990-06-15"})
        self.client_rec.refresh_from_db()
        self.assertEqual(self.client_rec.date_of_birth, date(1990, 6, 15))

    def test_a_future_date_is_refused_here_too(self):
        self._as(self.admin).post(
            reverse("clients:client_kyc_update_dob", args=[self.client_rec.id]),
            {"date_of_birth": (timezone.localdate() + timedelta(days=1)).isoformat()})
        self.client_rec.refresh_from_db()
        self.assertIsNone(self.client_rec.date_of_birth)

    def test_an_employee_cannot_touch_someone_elses_client(self):
        resp = self._as(self.other).post(
            reverse("clients:client_kyc_update_dob", args=[self.client_rec.id]),
            {"date_of_birth": "1990-06-15"})
        self.assertEqual(resp.status_code, 403)

    def test_a_client_who_is_already_past_forty_is_pointed_at_the_backlog(self):
        """Typing a 45-year-old's date of birth is how they first become
        visible — the daily cron only ever sees today's birthdays."""
        resp = self._as(self.admin).post(
            reverse("clients:client_kyc_update_dob", args=[self.client_rec.id]),
            {"date_of_birth": _dob_for_age(45).isoformat()}, follow=True)
        self.assertContains(resp, "retirement_alerts --backlog")


class RetirementAlertTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        au = User.objects.create_user("ret_admin", password="pw", first_name="Boss")
        cls.admin = Employee.objects.create(user=au, role="admin", salary=0, active=True)
        eu = User.objects.create_user("ret_emp", password="pw", first_name="Mansi")
        cls.emp = Employee.objects.create(user=eu, role="employee", salary=0, active=True)

    def _client(self, cid, age, mapped=True):
        return Client.objects.create(
            id=cid, name=f"Client {cid}", date_of_birth=_dob_for_age(age),
            mapped_to=self.emp if mapped else None)

    # ── the trigger ──
    def test_turning_forty_today_raises_a_task_for_the_mapped_employee(self):
        c = self._client(7301, 40)
        client_alerts.run_retirement(client_alerts.turning_age_today())
        task = Task.objects.get(assign_group=f"retire:{c.id}", assigned_to=self.emp)
        self.assertEqual(task.assigned_to, self.emp)
        self.assertEqual(task.client, c)
        self.assertEqual(task.priority, Task.PRIORITY_HIGH)
        self.assertIn("retirement planning", task.title.lower())
        # A due time is what arms tasks_ring_due and the phone's own alarm.
        self.assertIsNotNone(task.due_time)

    def test_the_mapped_employee_and_every_admin_each_get_their_own_row(self):
        """A sibling row per person is what lets each of them close their own
        copy — the owner asked for the task to be allocated, not just seen."""
        c = self._client(7302, 40)
        client_alerts.run_retirement(client_alerts.turning_age_today())
        rows = Task.objects.filter(assign_group=f"retire:{c.id}")
        self.assertEqual({t.assigned_to_id for t in rows},
                         {self.emp.id, self.admin.id})

    def test_thirty_nine_and_forty_one_are_left_alone(self):
        self._client(7303, 39)
        self._client(7304, 41)
        created, _ = client_alerts.run_retirement(client_alerts.turning_age_today())
        self.assertEqual(created, 0)

    def test_a_client_with_no_date_of_birth_is_never_alerted(self):
        Client.objects.create(id=7305, name="Blank", mapped_to=self.emp)
        created, _ = client_alerts.run_retirement(client_alerts.turning_age_today())
        self.assertEqual(created, 0)

    def test_running_twice_raises_nothing_new(self):
        c = self._client(7306, 40)
        client_alerts.run_retirement(client_alerts.turning_age_today())
        before = Task.objects.filter(assign_group=f"retire:{c.id}").count()
        created, skipped = client_alerts.run_retirement(client_alerts.turning_age_today())
        self.assertEqual((created, skipped), (0, 1))
        self.assertEqual(Task.objects.filter(assign_group=f"retire:{c.id}").count(), before)

    def test_an_unmapped_client_falls_to_the_admins(self):
        c = self._client(7307, 40, mapped=False)
        client_alerts.run_retirement(client_alerts.turning_age_today())
        rows = Task.objects.filter(assign_group=f"retire:{c.id}")
        self.assertEqual({t.assigned_to_id for t in rows}, {self.admin.id},
                         "an unmapped client's alert must not fall in a hole")

    # ── the backlog ──
    def test_the_backlog_finds_everyone_already_past_forty(self):
        self._client(7308, 45)
        self._client(7309, 40)
        self._client(7310, 30)
        ids = {c.id for c in client_alerts.retirement_backlog()}
        self.assertEqual(ids, {7308, 7309})

    def test_the_backlog_skips_clients_already_alerted(self):
        c = self._client(7311, 50)
        client_alerts.raise_retirement_alert(c)
        self.assertEqual(client_alerts.retirement_backlog(), [])

    def test_the_daily_command_does_not_touch_the_backlog(self):
        """Otherwise the first morning after deploy raises hundreds of tasks."""
        from django.core.management import call_command
        self._client(7312, 55)
        call_command("retirement_alerts")
        self.assertEqual(Task.objects.filter(assign_group__startswith="retire:").count(), 0)

    def test_the_backlog_flag_is_a_dry_run_without_apply(self):
        from django.core.management import call_command
        self._client(7313, 55)
        call_command("retirement_alerts", "--backlog")
        self.assertEqual(Task.objects.filter(assign_group__startswith="retire:").count(), 0)
        call_command("retirement_alerts", "--backlog", "--apply")
        # One group (several sibling rows — the employee and every admin).
        self.assertEqual(
            Task.objects.filter(assign_group__startswith="retire:")
            .values("assign_group").distinct().count(), 1)


class MergeByNameTests(TestCase):
    """Merging duplicates found by name — the near-misses the automatic
    duplicate groups (exact PAN / phone / name) never catch."""

    @classmethod
    def setUpTestData(cls):
        au = User.objects.create_user("mg_admin", password="pw")
        cls.admin = Employee.objects.create(user=au, role="admin", salary=0, active=True)
        eu = User.objects.create_user("mg_emp", password="pw")
        cls.emp = Employee.objects.create(user=eu, role="employee", salary=0, active=True)
        cls.a = Client.objects.create(id=7401, name="RAJESH SHARMA", phone="9876500033")
        cls.b = Client.objects.create(id=7402, name="SHARMA RAJESH KUMAR", phone="9876500044")
        cls.other = Client.objects.create(id=7403, name="PRIYA DESHMUKH")

    def _as(self, emp):
        c = DjangoClient()
        c.force_login(emp.user)
        return c

    def _merge_section(self, html):
        """Just the merge card — the page also lists these clients under
        missing PAN / missing DOB, so a whole-page assertion proves nothing."""
        start = html.index('id="merge-search"')
        return html[start:html.index('id="duplicates"', start)]

    def test_searching_a_name_finds_the_word_order_variant(self):
        html = self._as(self.admin).get(
            reverse("clients:client_kyc_issues") + "?mq=rajesh+sharma").content.decode()
        section = self._merge_section(html)
        self.assertIn("SHARMA RAJESH KUMAR", section)
        self.assertIn("RAJESH SHARMA", section)
        self.assertNotIn("PRIYA DESHMUKH", section,
                         "the picker must not offer an unrelated client to merge")

    def test_the_search_box_is_admin_only(self):
        html = self._as(self.emp).get(
            reverse("clients:client_kyc_issues") + "?mq=rajesh").content.decode()
        self.assertNotIn("Merge ticked into Keep", html)

    def test_the_ticked_profile_merges_into_the_keeper(self):
        self._as(self.admin).post(reverse("clients:client_merge"),
                                  {"keep_id": str(self.a.id), "remove_id": [str(self.b.id)]})
        self.assertTrue(Client.objects.filter(id=self.a.id).exists())
        self.assertFalse(Client.objects.filter(id=self.b.id).exists())

    def test_an_employee_cannot_merge(self):
        resp = self._as(self.emp).post(reverse("clients:client_merge"),
                                       {"keep_id": str(self.a.id), "remove_id": [str(self.b.id)]})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Client.objects.filter(id=self.b.id).exists())

    def test_a_merge_moves_the_business_records(self):
        from clients.models import Sale
        Sale.objects.create(client=self.b, employee=self.emp, product="SIP",
                            amount=5000, date=date(2026, 3, 1))
        self._as(self.admin).post(reverse("clients:client_merge"),
                                  {"keep_id": str(self.a.id), "remove_id": [str(self.b.id)]})
        self.assertEqual(Sale.objects.filter(client=self.a).count(), 1)


class BirthdayTaskTests(TestCase):
    """Every client, every year, on the day: wish them, send the valuation
    report, review the plan — for the mapped employee AND every admin."""

    @classmethod
    def setUpTestData(cls):
        au = User.objects.create_user("bd_admin", password="pw", first_name="Boss")
        cls.admin = Employee.objects.create(user=au, role="admin", salary=0, active=True)
        eu = User.objects.create_user("bd_emp", password="pw", first_name="Mansi")
        cls.emp = Employee.objects.create(user=eu, role="employee", salary=0, active=True)

    def _client(self, cid, dob, mapped=True):
        return Client.objects.create(id=cid, name=f"Client {cid}", date_of_birth=dob,
                                     mapped_to=self.emp if mapped else None)

    def _today_dob(self, years=35):
        return _dob_for_age(years)

    def test_a_birthday_today_raises_the_task(self):
        c = self._client(7601, self._today_dob())
        client_alerts.run_birthdays(client_alerts.birthdays_today())
        rows = Task.objects.filter(assign_group=f"bday:{c.id}:{timezone.localdate().year}")
        self.assertTrue(rows.exists())
        self.assertIn("Birthday", rows.first().title)

    def test_it_goes_to_the_mapped_employee_and_every_admin(self):
        c = self._client(7602, self._today_dob())
        client_alerts.run_birthdays(client_alerts.birthdays_today())
        rows = Task.objects.filter(assign_group=f"bday:{c.id}:{timezone.localdate().year}")
        self.assertEqual({t.assigned_to_id for t in rows}, {self.emp.id, self.admin.id})

    def test_the_three_actions_ride_as_a_checklist(self):
        c = self._client(7603, self._today_dob())
        client_alerts.run_birthdays(client_alerts.birthdays_today())
        task = Task.objects.filter(
            assign_group=f"bday:{c.id}:{timezone.localdate().year}").first()
        items = [i.title.lower() for i in task.checklist_items.all()]
        self.assertEqual(len(items), 3)
        self.assertTrue(any("wish" in i for i in items))
        self.assertTrue(any("valuation" in i for i in items))
        self.assertTrue(any("financial plan" in i or "plan" in i for i in items))

    def test_it_is_medium_priority(self):
        """High/critical re-ring every four hours until acknowledged — a daily
        task at that volume is a storm people learn to swipe away."""
        c = self._client(7604, self._today_dob())
        client_alerts.run_birthdays(client_alerts.birthdays_today())
        task = Task.objects.filter(
            assign_group=f"bday:{c.id}:{timezone.localdate().year}").first()
        self.assertEqual(task.priority, Task.PRIORITY_MEDIUM)

    def test_a_birthday_on_another_day_is_left_alone(self):
        self._client(7605, self._today_dob() + timedelta(days=40))
        created, _ = client_alerts.run_birthdays(client_alerts.birthdays_today())
        self.assertEqual(created, 0)

    def test_a_client_with_no_date_of_birth_is_never_wished(self):
        Client.objects.create(id=7606, name="Blank", mapped_to=self.emp)
        created, _ = client_alerts.run_birthdays(client_alerts.birthdays_today())
        self.assertEqual(created, 0)

    def test_running_twice_in_a_day_raises_one_group(self):
        c = self._client(7607, self._today_dob())
        client_alerts.run_birthdays(client_alerts.birthdays_today())
        before = Task.objects.filter(client=c).count()
        created, skipped = client_alerts.run_birthdays(client_alerts.birthdays_today())
        self.assertEqual((created, skipped), (0, 1))
        self.assertEqual(Task.objects.filter(client=c).count(), before)

    def test_next_year_is_a_new_task(self):
        """The key carries the year, so the birthday comes round again — a
        lifetime dedup would wish each client exactly once, ever."""
        today = timezone.localdate()
        c = self._client(7608, self._today_dob())
        client_alerts.run_birthdays([c], today=today)
        next_year = today.replace(year=today.year + 1) if today.day != 29 or today.month != 2 \
            else today.replace(year=today.year + 1, day=28)
        client_alerts.run_birthdays([c], today=next_year)
        groups = set(Task.objects.filter(client=c).values_list("assign_group", flat=True))
        self.assertEqual(len(groups), 2, groups)

    def test_an_unmapped_client_falls_to_the_admins(self):
        c = self._client(7609, self._today_dob(), mapped=False)
        client_alerts.run_birthdays(client_alerts.birthdays_today())
        rows = Task.objects.filter(assign_group=f"bday:{c.id}:{timezone.localdate().year}")
        self.assertEqual({t.assigned_to_id for t in rows}, {self.admin.id})

    def test_the_command_is_a_dry_run_with_the_flag(self):
        from django.core.management import call_command
        self._client(7610, self._today_dob())
        call_command("client_birthday_tasks", "--dry-run")
        self.assertEqual(Task.objects.filter(assign_group__startswith="bday:").count(), 0)
        call_command("client_birthday_tasks")
        self.assertTrue(Task.objects.filter(assign_group__startswith="bday:").exists())

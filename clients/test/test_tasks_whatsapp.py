"""Coverage for the WhatsApp task notifications + daily digest (Meta Cloud API).

No real HTTP is made: services/whatsapp.send_template is a no-op unless the two
env vars are set, so these tests assert (a) the no-op path records a "skipped"
MessageLog, (b) the digest command computes the right per-employee counts and
sends via the template, and (c) task assignment triggers a WhatsApp send.

Run: .venv/bin/python manage.py test clients.test.test_tasks_whatsapp -v 2
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.management.commands.tasks_send_daily_digest import digest_counts
from clients.models import Employee, MessageLog, Task, TaskReminderSetting


class WhatsAppServiceTests(TestCase):
    def test_no_op_when_unconfigured_records_skipped(self):
        from clients.services import whatsapp

        with mock.patch.dict("os.environ", {}, clear=False):
            for key in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"):
                whatsapp.os.environ.pop(key, None)
            ok = whatsapp.send_template("9876543210", "task_assigned", ["a", "b"])

        self.assertFalse(ok)
        log = MessageLog.objects.latest("created_at")
        self.assertEqual(log.status, "skipped")
        self.assertEqual(log.recipient_phone, "919876543210")

    def test_invalid_phone_returns_false_no_log(self):
        from clients.services import whatsapp
        before = MessageLog.objects.count()
        self.assertFalse(whatsapp.send_template("", "task_assigned", []))
        self.assertEqual(MessageLog.objects.count(), before)

    def test_configured_posts_to_meta(self):
        from clients.services import whatsapp

        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"messages": [{"id": "wamid.TEST"}]}
        env = {"WHATSAPP_PHONE_NUMBER_ID": "123", "WHATSAPP_ACCESS_TOKEN": "tok"}
        with mock.patch.dict("os.environ", env), \
                mock.patch.object(whatsapp.requests, "post", return_value=fake) as post:
            ok = whatsapp.send_template("9876543210", "task_assigned", ["x"])

        self.assertTrue(ok)
        post.assert_called_once()
        url = post.call_args.args[0]
        self.assertIn("/123/messages", url)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["to"], "919876543210")
        self.assertEqual(payload["template"]["name"], "task_assigned")
        log = MessageLog.objects.latest("created_at")
        self.assertEqual(log.status, "sent")
        self.assertEqual(log.provider_message_id, "wamid.TEST")


class DigestCountsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user(username="emp1", password="pw", first_name="Emp")
        cls.emp = Employee.objects.create(user=cls.u, role="employee", active=True,
                                          phone="9876543210")
        cls.admin = User.objects.create_user(username="adm", password="pw")
        Employee.objects.create(user=cls.admin, role="admin", active=True)

    def _task(self, status, due_offset=None):
        due = None
        if due_offset is not None:
            due = timezone.localdate() + timedelta(days=due_offset)
        return Task.objects.create(title="t", status=status, assigned_to=self.emp,
                                   due_date=due, created_by=self.admin)

    def test_counts_by_status_and_this_week(self):
        self._task(Task.STATUS_OVERDUE)
        self._task(Task.STATUS_PENDING, due_offset=3)      # counts in pending + this_week
        self._task(Task.STATUS_IN_PROGRESS, due_offset=30)  # in_progress, not this_week
        self._task(Task.STATUS_COMPLETED, due_offset=1)     # ignored
        self._task(Task.STATUS_PENDING, due_offset=10)      # pending, not this_week

        overdue, pending, in_progress, this_week = digest_counts(self.emp)
        self.assertEqual((overdue, pending, in_progress, this_week), (1, 2, 1, 1))

    def test_digest_command_skips_empty_and_sends_for_active(self):
        self._task(Task.STATUS_PENDING, due_offset=2)
        setting = TaskReminderSetting.current()
        setting.send_daily_digest = True
        setting.save()

        with mock.patch(
            "clients.management.commands.tasks_send_daily_digest.send_template",
            return_value=True,
        ) as send:
            call_command("tasks_send_daily_digest", "--force")

        send.assert_called_once()
        phone, template, variables = send.call_args.args
        self.assertEqual(phone, "9876543210")
        self.assertEqual(template, "task_daily_digest")
        # {{1}} name, {{2}} overdue, {{3}} pending, {{4}} in_progress, {{5}} this_week
        self.assertEqual(variables[2], "1")  # one pending task

    def test_digest_disabled_sends_nothing(self):
        self._task(Task.STATUS_PENDING, due_offset=2)
        setting = TaskReminderSetting.current()
        setting.send_daily_digest = False
        setting.save()
        with mock.patch(
            "clients.management.commands.tasks_send_daily_digest.send_template",
        ) as send:
            call_command("tasks_send_daily_digest", "--force")
        send.assert_not_called()


class AssignmentTriggersWhatsAppTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_u = User.objects.create_user(username="adm2", password="pw",
                                               first_name="Boss")
        cls.admin = Employee.objects.create(user=cls.admin_u, role="admin", active=True)
        cls.emp_u = User.objects.create_user(username="emp2", password="pw", first_name="Worker")
        cls.emp = Employee.objects.create(user=cls.emp_u, role="employee", active=True,
                                          phone="9876500000")

    def test_create_task_sends_whatsapp_to_assignee(self):
        from django.test import Client
        c = Client()
        c.force_login(self.admin_u)
        with mock.patch("clients.services.whatsapp.send_template", return_value=True) as send:
            resp = c.post(reverse("clients:task_create"), {
                "title": "Call client", "priority": "high",
                "assigned_to": [str(self.emp.pk)],
            })
        self.assertIn(resp.status_code, (200, 302))
        send.assert_called_once()
        phone, template, _variables = send.call_args.args
        self.assertEqual(phone, "9876500000")
        self.assertEqual(template, "task_assigned")

    def test_no_phone_no_whatsapp(self):
        from django.test import Client
        self.emp.phone = ""
        self.emp.save()
        c = Client()
        c.force_login(self.admin_u)
        with mock.patch("clients.services.whatsapp.send_template", return_value=True) as send:
            c.post(reverse("clients:task_create"), {
                "title": "No phone task", "priority": "low",
                "assigned_to": [str(self.emp.pk)],
            })
        send.assert_not_called()

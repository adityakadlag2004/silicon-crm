"""End-to-end coverage for the Task Management module (Phase 1).

Drives the real HTTP flow with the Django test client: page access by role,
task creation with checklist + subscriber, checklist toggle, comments, status
changes with notification fan-out, soft-delete/restore, the overdue cron, and
category management. Google Drive is never touched (no file uploads here), so
these run offline.

Run: .venv/bin/python manage.py test clients.test.test_tasks_module -v 2
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Employee,
    Notification,
    Task,
    TaskActivity,
    TaskCategory,
    TaskChecklistItem,
    TaskSubscriber,
)


class TaskModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for role in ("admin", "manager", "employee"):
            u = User.objects.create_user(username=f"t_{role}", password="pw",
                                         email=f"{role}@t.local", first_name=role.title())
            Employee.objects.create(user=u, role=role, salary=0, active=True)
            cls.users[role] = u
        # A second employee to assign to / loop in.
        cls.other = User.objects.create_user(username="t_other", password="pw",
                                             first_name="Other")
        cls.other_emp = Employee.objects.create(user=cls.other, role="employee",
                                                 salary=0, active=True)
        cls.category = TaskCategory.objects.create(name="Ops", created_by=cls.users["admin"])

    def client_for(self, role):
        c = Client()
        c.force_login(self.users[role] if role != "other" else self.other)
        return c

    # ---- page access ----
    def test_all_pages_load(self):
        c = self.client_for("admin")
        for name in ("task_dashboard", "task_my", "task_delegated", "task_subscribed",
                     "task_all", "task_deleted", "task_activities", "task_categories"):
            resp = c.get(reverse(f"clients:{name}"))
            self.assertEqual(resp.status_code, 200, f"{name} -> {resp.status_code}")

    def test_employee_cannot_reach_categories(self):
        resp = self.client_for("employee").get(reverse("clients:task_categories"))
        self.assertEqual(resp.status_code, 403)

    # ---- creation + notifications ----
    def _create_task(self, actor="admin"):
        c = self.client_for(actor)
        resp = c.post(reverse("clients:task_create"), {
            "title": "Prepare month-end report",
            "description": "Compile all numbers.",
            "priority": "high",
            "category": self.category.id,
            "assigned_to": self.other_emp.id,
            "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            "due_time": "17:00",
            "checklist_item": ["Gather data", "Draft", "Review"],
            "subscribers": [self.users["manager"].id],
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        return Task.objects.latest("id")

    def test_create_populates_everything(self):
        Notification.objects.all().delete()
        task = self._create_task()
        self.assertEqual(task.title, "Prepare month-end report")
        self.assertEqual(task.assigned_to, self.other_emp)
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.checklist_items.count(), 3)
        self.assertTrue(TaskSubscriber.objects.filter(task=task, user=self.users["manager"]).exists())
        self.assertTrue(task.activities.filter(action=TaskActivity.CREATED).exists())
        self.assertTrue(task.activities.filter(action=TaskActivity.ASSIGNED).exists())
        # Assignee + subscriber both notified (creator excluded).
        self.assertTrue(Notification.objects.filter(recipient=self.other).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.users["manager"]).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.users["admin"]).exists())

    def test_checklist_toggle_updates_percent(self):
        task = self._create_task()
        item = task.checklist_items.first()
        c = self.client_for("admin")
        c.post(reverse("clients:task_toggle_checklist", args=[task.pk, item.id]))
        item.refresh_from_db()
        self.assertTrue(item.is_done)
        self.assertEqual(task.checklist_percent, 33)

    def test_status_change_notifies_and_completes(self):
        task = self._create_task()
        Notification.objects.all().delete()
        c = self.client_for("admin")
        c.post(reverse("clients:task_set_status", args=[task.pk]), {"status": "completed"})
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertIsNotNone(task.completed_at)
        self.assertTrue(task.activities.filter(action=TaskActivity.COMPLETED).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.other).exists())

    def test_comment_and_activity(self):
        task = self._create_task()
        c = self.client_for("admin")
        c.post(reverse("clients:task_add_comment", args=[task.pk]), {"body": "Any update?"})
        self.assertEqual(task.comments.count(), 1)
        self.assertTrue(task.activities.filter(action=TaskActivity.COMMENT_ADDED).exists())

    def test_soft_delete_and_restore(self):
        task = self._create_task()
        c = self.client_for("admin")
        c.post(reverse("clients:task_delete", args=[task.pk]))
        task.refresh_from_db()
        self.assertTrue(task.is_deleted)
        # Shows in the recycle bin.
        resp = c.get(reverse("clients:task_deleted"))
        self.assertContains(resp, f"#{task.pk}" if False else task.title)
        c.post(reverse("clients:task_restore", args=[task.pk]))
        task.refresh_from_db()
        self.assertFalse(task.is_deleted)

    def test_permission_employee_cannot_edit_others_task(self):
        task = self._create_task(actor="admin")  # assigned to other_emp
        # A different employee (not creator/assignee/subscriber) is blocked.
        resp = self.client_for("employee").post(
            reverse("clients:task_set_status", args=[task.pk]), {"status": "completed"})
        self.assertIn(resp.status_code, (403, 404))
        task.refresh_from_db()
        self.assertNotEqual(task.status, "completed")

    def test_overdue_command_flips_and_notifies(self):
        task = self._create_task()
        # Force a past deadline.
        task.due_date = timezone.localdate() - timedelta(days=1)
        task.due_time = None
        task.status = Task.STATUS_PENDING
        task.save()
        Notification.objects.all().delete()
        call_command("tasks_mark_overdue")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_OVERDUE)
        self.assertTrue(Notification.objects.filter(recipient=self.other, title="Task overdue").exists())

    def test_category_create_and_toggle(self):
        c = self.client_for("admin")
        c.post(reverse("clients:task_categories"),
               {"action": "create", "name": "Finance", "color": "#123456", "icon": "bi-cash"})
        cat = TaskCategory.objects.get(name="Finance")
        self.assertTrue(cat.is_active)
        c.post(reverse("clients:task_categories"), {"action": "toggle", "id": cat.id})
        cat.refresh_from_db()
        self.assertFalse(cat.is_active)

    # ---- ringing task alarms + app status/due editing (v4.13) ----

    def test_assignment_and_comment_ring_as_task_alarm(self):
        """Task assignment and comments send one data-only task_alarm push
        (the app rings it like an alarm); the plain FCM mirror stays silent
        so nobody gets a duplicate tray notification."""
        import json
        from unittest import mock

        with mock.patch("clients.services.push.send_data_push_to_user") as data_push, \
                mock.patch("clients.services.push.send_push_to_user") as plain_push:
            task = self._create_task()
            # Assignee + subscribed manager both ring.
            rung = {call.args[0] for call in data_push.call_args_list}
            self.assertIn(self.other, rung)
            payload = data_push.call_args_list[0].args[1]
            self.assertEqual(payload["kind"], "task_alarm")
            self.assertIn(task.title, payload["body"])
            plain_push.assert_not_called()

            data_push.reset_mock()
            resp = self.client_for("admin").post(
                reverse("clients:app_task_action", args=[task.pk]),
                data=json.dumps({"action": "comment", "body": "please prioritise"}),
                content_type="application/json",
            )
            self.assertTrue(resp.json()["ok"])
            self.assertTrue(data_push.called)
            self.assertEqual(data_push.call_args.args[1]["kind"], "task_alarm")
            plain_push.assert_not_called()
        # In-app notification rows still exist for the Notifications screen.
        self.assertTrue(Notification.objects.filter(recipient=self.other).exists())

    def test_app_action_status_and_due_update(self):
        """The phone can move a task through any status and change its due
        date/time via the action endpoint (assignee permissions suffice)."""
        import json
        task = self._create_task()
        c = Client()
        c.force_login(self.other)  # the assignee, a plain employee

        resp = c.post(reverse("clients:app_task_action", args=[task.pk]),
                      data=json.dumps({"action": "status", "status": "in_progress"}),
                      content_type="application/json")
        self.assertEqual(resp.json()["status"], "in_progress")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_IN_PROGRESS)

        new_due = (timezone.localdate() + timedelta(days=7)).isoformat()
        resp = c.post(reverse("clients:app_task_action", args=[task.pk]),
                      data=json.dumps({"action": "due", "due_date": new_due, "due_time": "15:30"}),
                      content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        task.refresh_from_db()
        self.assertEqual(task.due_date.isoformat(), new_due)
        self.assertEqual(task.due_time.strftime("%H:%M"), "15:30")


class TaskV414Tests(TestCase):
    """v4.14: due-time rings, acknowledgement, escalation, mentions,
    client linking, templates, today feed, timeliness."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="v_admin", password="pw")
        Employee.objects.create(user=cls.admin, role="admin", salary=0, active=True)
        cls.emp_user = User.objects.create_user(username="v_emp", password="pw", first_name="Ravi")
        cls.emp = Employee.objects.create(user=cls.emp_user, role="employee", salary=0, active=True)

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def _task(self, **kw):
        defaults = dict(title="Collect KYC", created_by=self.admin, assigned_to=self.emp,
                        priority="high", status=Task.STATUS_PENDING)
        defaults.update(kw)
        return Task.objects.create(**defaults)

    def _fixed_11am(self):
        return timezone.localtime().replace(hour=11, minute=0, second=0, microsecond=0)

    # ── due-time ring ──
    def test_tasks_ring_due_rings_once_at_due_time(self):
        from unittest import mock
        fixed = self._fixed_11am()
        task = self._task(priority="medium",
                          due_date=fixed.date(), due_time=(fixed - timedelta(minutes=5)).time())
        with mock.patch("django.utils.timezone.localtime", return_value=fixed), \
                mock.patch("clients.management.commands.tasks_ring_due.send_data_push_to_user") as push:
            call_command("tasks_ring_due")
            push.assert_called_once()
            user, payload = push.call_args.args
            self.assertEqual(user, self.emp_user)
            self.assertEqual(payload["kind"], "task_alarm")
            self.assertEqual(payload["task_id"], task.pk)
            push.reset_mock()
            call_command("tasks_ring_due")  # marker set → silent
            push.assert_not_called()
        task.refresh_from_db()
        self.assertIsNotNone(task.due_alarm_sent_at)
        # In-app notification exists but skipped the plain FCM mirror.
        self.assertTrue(Notification.objects.filter(
            recipient=self.emp_user, title__icontains="due now").exists())

    def test_due_edit_rearms_ring(self):
        import json as _json
        fixed = self._fixed_11am()
        task = self._task(due_date=fixed.date(), due_time=fixed.time())
        Task.objects.filter(pk=task.pk).update(
            due_alarm_sent_at=timezone.now(), reminded_same_day=True, reminded_day_before=True)
        resp = self._client(self.emp_user).post(
            reverse("clients:app_task_action", args=[task.pk]),
            data=_json.dumps({"action": "due",
                              "due_date": (fixed.date() + timedelta(days=1)).isoformat(),
                              "due_time": "16:00"}),
            content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        task.refresh_from_db()
        self.assertIsNone(task.due_alarm_sent_at)
        self.assertFalse(task.reminded_same_day)
        self.assertFalse(task.reminded_day_before)

    # ── acknowledgement ──
    def test_acknowledge_flow(self):
        import json as _json
        task = self._task()
        # Only the assignee may acknowledge.
        resp = self._client(self.admin).post(
            reverse("clients:app_task_action", args=[task.pk]),
            data=_json.dumps({"action": "acknowledge"}), content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        resp = self._client(self.emp_user).post(
            reverse("clients:app_task_action", args=[task.pk]),
            data=_json.dumps({"action": "acknowledge"}), content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        task.refresh_from_db()
        self.assertIsNotNone(task.acknowledged_at)
        # The delegator hears about it.
        self.assertTrue(Notification.objects.filter(
            recipient=self.admin, title="Task acknowledged").exists())

    def test_status_change_by_assignee_auto_acknowledges(self):
        import json as _json
        task = self._task()
        self._client(self.emp_user).post(
            reverse("clients:app_task_action", args=[task.pk]),
            data=_json.dumps({"action": "status", "status": "in_progress"}),
            content_type="application/json")
        task.refresh_from_db()
        self.assertIsNotNone(task.acknowledged_at)

    def test_unacknowledged_high_priority_rerings_with_backoff(self):
        from unittest import mock
        fixed = self._fixed_11am()
        task = self._task()  # high priority, no due date
        Task.objects.filter(pk=task.pk).update(created_at=fixed - timedelta(hours=5))
        with mock.patch("django.utils.timezone.localtime", return_value=fixed), \
                mock.patch("clients.management.commands.tasks_ring_due.send_data_push_to_user") as push:
            call_command("tasks_ring_due")
            push.assert_called_once()
            self.assertIn("acknowledgement", push.call_args.args[1]["title"].lower())
            push.reset_mock()
            call_command("tasks_ring_due")  # inside the 4h backoff → silent
            push.assert_not_called()
        task.refresh_from_db()
        self.assertIsNotNone(task.ack_last_rung_at)

    # ── completed_at hygiene ──
    def test_completed_at_cleared_on_reopen(self):
        import json as _json
        task = self._task(priority="low")
        c = self._client(self.emp_user)
        c.post(reverse("clients:app_task_action", args=[task.pk]),
               data=_json.dumps({"action": "status", "status": "completed"}),
               content_type="application/json")
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)
        c.post(reverse("clients:app_task_action", args=[task.pk]),
               data=_json.dumps({"action": "status", "status": "pending"}),
               content_type="application/json")
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    # ── overdue escalation ──
    def test_overdue_escalation_digest_once_per_day(self):
        from unittest import mock
        fixed = self._fixed_11am()
        task = self._task()
        Task.objects.filter(pk=task.pk).update(
            status=Task.STATUS_OVERDUE, due_date=fixed.date() - timedelta(days=3))
        with mock.patch("django.utils.timezone.localtime", return_value=fixed), \
                mock.patch("clients.management.commands.tasks_mark_overdue.send_data_push_to_user") as push:
            call_command("tasks_mark_overdue")
            notes = Notification.objects.filter(title__icontains="Overdue tasks need attention")
            self.assertTrue(notes.filter(recipient=self.admin).exists())
            before = notes.count()
            call_command("tasks_mark_overdue")  # same day → no duplicates
            self.assertEqual(
                Notification.objects.filter(title__icontains="Overdue tasks need attention").count(),
                before)
            self.assertTrue(push.called)
            self.assertIn("Ravi", push.call_args.args[1]["body"])

    # ── mentions ──
    def test_comment_mentions_ring_and_subscribe(self):
        import json as _json
        from unittest import mock
        from clients.models import TaskSubscriber
        task = self._task()
        with mock.patch("clients.services.push.send_data_push_to_user") as push:
            self._client(self.admin).post(
                reverse("clients:app_task_action", args=[task.pk]),
                data=_json.dumps({"action": "comment", "body": "@v_emp please check this today"}),
                content_type="application/json")
        self.assertTrue(TaskSubscriber.objects.filter(task=task, user=self.emp_user).exists())
        self.assertTrue(Notification.objects.filter(
            recipient=self.emp_user, title="You were mentioned").exists())
        # Mentioned user rang exactly once (no double ring from the fan-out).
        rings = [c for c in push.call_args_list if c.args[0] == self.emp_user]
        self.assertEqual(len(rings), 1)

    # ── client linking ──
    def test_task_client_link_and_profile_section(self):
        import json as _json
        from clients.models import Client as ClientModel
        cust = ClientModel.objects.create(id=990001, name="Sharma Ji", phone="9876500001")
        resp = self._client(self.admin).post(
            reverse("clients:app_task_create"),
            data=_json.dumps({"title": "Collect KYC from Sharma", "assigned_to": self.emp.id,
                              "client_id": cust.id}),
            content_type="application/json")
        task = Task.objects.get(pk=resp.json()["id"])
        self.assertEqual(task.client, cust)
        detail = self._client(self.emp_user).get(
            reverse("clients:app_task_detail", args=[task.pk])).json()
        self.assertEqual(detail["client"], "Sharma Ji")
        self.assertEqual(detail["client_phone"], "9876500001")
        # Web client profile shows the open task.
        page = self._client(self.admin).get(
            reverse("clients:client_profile", args=[cust.id]))
        self.assertContains(page, "Collect KYC from Sharma")

    # ── templates ──
    def test_templates_save_and_list(self):
        import json as _json
        resp = self._client(self.admin).post(
            reverse("clients:app_task_template_save"),
            data=_json.dumps({"name": "Onboarding", "title": "Onboard new client",
                              "priority": "high", "checklist": ["KYC", "Risk profile", "First SIP"]}),
            content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        data = self._client(self.emp_user).get(reverse("clients:app_task_templates")).json()
        self.assertEqual(data["templates"][0]["name"], "Onboarding")
        self.assertEqual(data["templates"][0]["checklist"], ["KYC", "Risk profile", "First SIP"])
        # Plain employees cannot create templates.
        resp = self._client(self.emp_user).post(
            reverse("clients:app_task_template_save"),
            data=_json.dumps({"name": "X", "title": "Y"}), content_type="application/json")
        self.assertEqual(resp.status_code, 403)

    # ── today feed ──
    def test_today_endpoint_combines_tasks_followups_renewals(self):
        from clients.models import CallFollowUp, Client as ClientModel, Renewal
        today = timezone.localdate()
        self._task(title="Due today", due_date=today, due_time=None, priority="low")
        cust = ClientModel.objects.create(id=990002, name="Verma", phone="9876500002")
        CallFollowUp.objects.create(employee=self.emp, phone="9876500002", client=cust,
                                    scheduled_at=timezone.now())
        Renewal.objects.create(client=cust, product_type="health", product_name="Care",
                               renewal_date=today, frequency="yearly", employee=self.emp,
                               premium_amount=5000)
        data = self._client(self.emp_user).get(reverse("clients:app_today")).json()
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["title"], "Due today")
        self.assertEqual(len(data["followups"]), 1)
        self.assertEqual(len(data["renewals"]), 1)
        self.assertEqual(data["renewals"][0]["client"], "Verma")

    # ── scorecard timeliness ──
    def test_scorecard_reports_on_time_vs_late(self):
        today = timezone.localdate()
        t1 = self._task(title="On time", due_date=today + timedelta(days=1), priority="low")
        Task.objects.filter(pk=t1.pk).update(status=Task.STATUS_COMPLETED, completed_at=timezone.now())
        t2 = self._task(title="Late", due_date=today - timedelta(days=1), priority="low")
        Task.objects.filter(pk=t2.pk).update(status=Task.STATUS_COMPLETED, completed_at=timezone.now())
        data = self._client(self.admin).get(
            reverse("clients:app_task_scorecard") + "?period=month").json()
        row = next(r for r in data["scorecard"] if r["name"] == "Ravi")
        self.assertEqual(row["on_time"], 1)
        self.assertEqual(row["late"], 1)
        self.assertEqual(row["on_time_pct"], 50)

    # ── v4.14.1: overdue is deadline truth; In Progress must stick ──

    def test_pending_past_due_reports_and_filters_as_overdue_without_cron(self):
        """A pending task past its due moment shows as Overdue in the app
        immediately — even if tasks_mark_overdue hasn't run."""
        task = self._task(title="Blown deadline", priority="low",
                          due_date=timezone.localdate() - timedelta(days=1))
        c = self._client(self.emp_user)
        rows = c.get(reverse("clients:app_tasks") + "?tab=my&status=overdue").json()["tasks"]
        self.assertIn(task.pk, [r["id"] for r in rows])
        row = next(r for r in rows if r["id"] == task.pk)
        self.assertEqual(row["status"], "overdue")
        self.assertTrue(row["late"])
        # …and it is NOT in the Pending tab.
        rows = c.get(reverse("clients:app_tasks") + "?tab=my&status=pending").json()["tasks"]
        self.assertNotIn(task.pk, [r["id"] for r in rows])
        # DB row untouched (the cron will persist the flip later).
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_PENDING)

    def test_in_progress_sticks_on_past_due_tasks(self):
        """Marking an overdue task In Progress must survive the cron —
        previously it flipped back to Overdue within 15 minutes."""
        import json as _json
        task = self._task(due_date=timezone.localdate() - timedelta(days=1))
        Task.objects.filter(pk=task.pk).update(status=Task.STATUS_OVERDUE)
        self._client(self.emp_user).post(
            reverse("clients:app_task_action", args=[task.pk]),
            data=_json.dumps({"action": "status", "status": "in_progress"}),
            content_type="application/json")
        call_command("tasks_mark_overdue")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_IN_PROGRESS)
        # The row still carries the deadline truth for the UI.
        detail = self._client(self.emp_user).get(
            reverse("clients:app_task_detail", args=[task.pk])).json()
        self.assertTrue(detail["late"])

    def test_reset_to_pending_past_due_stores_overdue(self):
        import json as _json
        task = self._task(due_date=timezone.localdate() - timedelta(days=1))
        Task.objects.filter(pk=task.pk).update(status=Task.STATUS_IN_PROGRESS)
        self._client(self.emp_user).post(
            reverse("clients:app_task_action", args=[task.pk]),
            data=_json.dumps({"action": "status", "status": "pending"}),
            content_type="application/json")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.STATUS_OVERDUE)

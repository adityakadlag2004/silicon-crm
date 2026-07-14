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

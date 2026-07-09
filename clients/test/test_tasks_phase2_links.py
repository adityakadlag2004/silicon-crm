"""Phase 2 (recurrence, reminders, prefs, saved filters) + Phase 3 (Business
Links) + native JSON API coverage.

Run: .venv/bin/python manage.py test clients.test.test_tasks_phase2_links -v 2
"""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Employee,
    Link,
    LinkCategory,
    LinkFavorite,
    Notification,
    NotificationPreference,
    RecurringTaskRule,
    SavedTaskFilter,
    Task,
    TaskCategory,
    TaskReminderSetting,
)
from clients.services.tasks import generate_recurring, next_due_date


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for role in ("admin", "employee"):
            u = User.objects.create_user(username=f"p2_{role}", password="pw",
                                         first_name=role.title())
            Employee.objects.create(user=u, role=role, salary=0, active=True)
            cls.users[role] = u
        cls.assignee = Employee.objects.get(user=cls.users["employee"])

    def c(self, role):
        cl = Client()
        cl.force_login(self.users[role])
        return cl


class RecurrenceTests(_Base):
    def _make_rule(self, freq, start, **kw):
        return RecurringTaskRule.objects.create(
            title="Daily standup", frequency=freq, interval=1,
            created_by=self.users["admin"], assigned_to=self.assignee,
            start_date=start, occurrences_created=1, last_generated_date=start,
            checklist_template="A\nB", subscriber_ids=str(self.users["admin"].id),
            **kw,
        )

    def test_next_due_daily_weekly_monthly(self):
        from datetime import date
        d = self._make_rule("daily", date(2026, 1, 1))
        self.assertEqual(next_due_date(d, date(2026, 1, 1)), date(2026, 1, 2))
        w = self._make_rule("weekly", date(2026, 1, 1), weekdays="0")  # Monday
        nd = next_due_date(w, date(2026, 1, 1))  # 2026-01-01 is a Thursday
        self.assertEqual(nd.weekday(), 0)
        m = self._make_rule("monthly", date(2026, 1, 31))
        self.assertEqual(next_due_date(m, date(2026, 1, 31)), date(2026, 2, 28))

    def test_generation_creates_instances_and_copies_checklist(self):
        start = timezone.localdate() - timedelta(days=3)
        rule = self._make_rule("daily", start)
        created = generate_recurring()
        self.assertGreaterEqual(created, 3)
        instances = Task.objects.filter(recurring_rule=rule)
        self.assertGreaterEqual(instances.count(), 3)
        # Checklist template copied.
        self.assertEqual(instances.first().checklist_items.count(), 2)
        # Subscriber copied.
        self.assertTrue(instances.first().subscribers.filter(user=self.users["admin"]).exists())

    def test_end_after_n_stops(self):
        start = timezone.localdate() - timedelta(days=10)
        rule = self._make_rule("daily", start, end_type=RecurringTaskRule.END_AFTER,
                               max_occurrences=3)
        generate_recurring()
        rule.refresh_from_db()
        # 1 initial (counted) + 2 generated = 3, then deactivates.
        self.assertEqual(rule.occurrences_created, 3)
        self.assertFalse(rule.is_active)

    def test_create_form_spawns_rule(self):
        resp = self.c("admin").post(reverse("clients:task_create"), {
            "title": "Weekly report", "priority": "medium",
            "assigned_to": self.assignee.id,
            "due_date": timezone.localdate().isoformat(),
            "repeat_rule": "weekly",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(RecurringTaskRule.objects.filter(title="Weekly report").exists())


class ReminderPrefTests(_Base):
    def test_notification_preference_mutes(self):
        # Employee mutes comment notifications.
        pref = NotificationPreference.objects.create(
            user=self.users["employee"], notify_comment_added=False)
        task = Task.objects.create(title="X", created_by=self.users["admin"],
                                   assigned_to=self.assignee)
        Notification.objects.all().delete()
        # Admin comments → employee is assignee but muted for comments.
        self.c("admin").post(reverse("clients:task_add_comment", args=[task.pk]),
                             {"body": "hello"})
        self.assertFalse(Notification.objects.filter(
            recipient=self.users["employee"], title__icontains="comment").exists())

    def test_reminders_day_before(self):
        setting = TaskReminderSetting.current()
        setting.remind_day_before = True
        setting.same_day_hour = timezone.localtime().hour  # act now
        setting.save()
        Task.objects.create(title="Due tomorrow", created_by=self.users["admin"],
                            assigned_to=self.assignee,
                            due_date=timezone.localdate() + timedelta(days=1),
                            status=Task.STATUS_PENDING)
        Notification.objects.all().delete()
        call_command("tasks_send_reminders")
        self.assertTrue(Notification.objects.filter(
            recipient=self.users["employee"], title="Task due tomorrow").exists())

    def test_saved_filter_roundtrip(self):
        c = self.c("admin")
        c.post(reverse("clients:task_save_filter"),
               {"name": "High only", "query_string": "priority=high"})
        sf = SavedTaskFilter.objects.get(user=self.users["admin"], name="High only")
        self.assertEqual(sf.query_string, "priority=high")
        c.post(reverse("clients:task_delete_filter", args=[sf.id]))
        self.assertFalse(SavedTaskFilter.objects.filter(pk=sf.id).exists())

    def test_settings_page_saves_prefs_for_admin(self):
        c = self.c("admin")
        resp = c.get(reverse("clients:task_settings"))
        self.assertEqual(resp.status_code, 200)
        # Uncheck everything by posting only scope.
        c.post(reverse("clients:task_settings"), {"scope": "prefs"})
        pref = NotificationPreference.objects.get(user=self.users["admin"])
        self.assertFalse(pref.notify_assigned)

    def test_settings_hidden_from_employee(self):
        resp = self.c("employee").get(reverse("clients:task_settings"))
        self.assertEqual(resp.status_code, 403)

    def test_task_category_quick_create(self):
        resp = self.c("admin").post(reverse("clients:task_category_create"),
                                    {"name": "Compliance", "color": "#ff0000"})
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], "Compliance")
        self.assertTrue(TaskCategory.objects.filter(name="Compliance").exists())

    def test_link_category_quick_create(self):
        resp = self.c("employee").post(reverse("clients:link_category_create"),
                                       {"name": "Insurance"})
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(LinkCategory.objects.filter(name="Insurance").exists())


class BusinessLinksTests(_Base):
    def test_dashboard_and_category_and_crud(self):
        c = self.c("employee")
        self.assertEqual(c.get(reverse("clients:links_dashboard")).status_code, 200)
        # Admin creates a category.
        self.c("admin").post(reverse("clients:link_categories"),
                             {"action": "create", "name": "Portals"})
        cat = LinkCategory.objects.get(name="Portals")
        # Employee creates a link.
        c.post(reverse("clients:link_create"),
               {"title": "CAMS", "url": "cams.com", "category": cat.id})
        link = Link.objects.get(title="CAMS")
        self.assertEqual(link.url, "https://cams.com")  # scheme auto-added
        self.assertEqual(link.created_by, self.users["employee"])
        # Category page renders.
        self.assertEqual(c.get(reverse("clients:links_category", args=[cat.id])).status_code, 200)
        # Favorite toggles.
        c.post(reverse("clients:link_favorite", args=[link.id]))
        self.assertTrue(LinkFavorite.objects.filter(user=self.users["employee"], link=link).exists())
        c.post(reverse("clients:link_favorite", args=[link.id]))
        self.assertFalse(LinkFavorite.objects.filter(user=self.users["employee"], link=link).exists())

    def test_employee_cannot_edit_others_link(self):
        admin_link = Link.objects.create(title="Admin only", url="https://a.com",
                                         created_by=self.users["admin"])
        resp = self.c("employee").post(
            reverse("clients:link_update", args=[admin_link.id]), {"title": "hacked"})
        self.assertEqual(resp.status_code, 403)

    def test_search_mode(self):
        Link.objects.create(title="Zerodha", url="https://z.com", created_by=self.users["admin"])
        resp = self.c("employee").get(reverse("clients:links_dashboard"), {"q": "zerodha"})
        self.assertContains(resp, "Zerodha")

    def test_employee_cannot_manage_categories(self):
        self.assertEqual(
            self.c("employee").get(reverse("clients:link_categories")).status_code, 403)


class NativeApiTests(_Base):
    def test_app_tasks_and_meta(self):
        Task.objects.create(title="N1", created_by=self.users["admin"], assigned_to=self.assignee)
        c = self.c("employee")
        data = c.get(reverse("clients:app_tasks"), {"tab": "my"}).json()
        self.assertIn("counts", data)
        self.assertTrue(any(t["title"] == "N1" for t in data["tasks"]))
        self.assertEqual(c.get(reverse("clients:app_task_meta")).status_code, 200)

    def test_app_task_create_and_action(self):
        c = self.c("admin")
        resp = c.post(reverse("clients:app_task_create"),
                      data=json.dumps({"title": "API task", "priority": "high",
                                       "assigned_to": self.assignee.id,
                                       "checklist": ["one", "two"]}),
                      content_type="application/json")
        tid = resp.json()["id"]
        task = Task.objects.get(pk=tid)
        self.assertEqual(task.checklist_items.count(), 2)
        # Toggle a checklist item via action.
        item = task.checklist_items.first()
        r2 = c.post(reverse("clients:app_task_action", args=[tid]),
                    data=json.dumps({"action": "checklist_toggle", "item_id": item.id}),
                    content_type="application/json")
        self.assertEqual(r2.json()["percent"], 50)

    def test_app_task_action_delete_and_due(self):
        c = self.c("admin")
        task = Task.objects.create(title="D", created_by=self.users["admin"],
                                   assigned_to=self.assignee)
        # change due
        c.post(reverse("clients:app_task_action", args=[task.pk]),
               data=json.dumps({"action": "due", "due_date": "2027-01-01"}),
               content_type="application/json")
        task.refresh_from_db()
        self.assertEqual(task.due_date.isoformat(), "2027-01-01")
        # soft delete
        c.post(reverse("clients:app_task_action", args=[task.pk]),
               data=json.dumps({"action": "delete"}), content_type="application/json")
        task.refresh_from_db()
        self.assertTrue(task.is_deleted)

    def test_app_task_create_with_repeat(self):
        c = self.c("admin")
        resp = c.post(reverse("clients:app_task_create"),
                      data=json.dumps({"title": "Standup", "repeat_rule": "daily",
                                       "due_date": timezone.localdate().isoformat()}),
                      content_type="application/json")
        tid = resp.json()["id"]
        self.assertTrue(RecurringTaskRule.objects.filter(instances__id=tid).exists())

    def test_app_task_activities_feed(self):
        Task.objects.create(title="Feed", created_by=self.users["admin"], assigned_to=self.assignee)
        data = self.c("admin").get(reverse("clients:app_task_activities")).json()
        self.assertIn("activities", data)

    def test_app_tasks_filter_by_priority(self):
        Task.objects.create(title="Crit", created_by=self.users["admin"],
                            assigned_to=self.assignee, priority="critical")
        Task.objects.create(title="Low", created_by=self.users["admin"],
                            assigned_to=self.assignee, priority="low")
        data = self.c("admin").get(reverse("clients:app_tasks"), {"priority": "critical"}).json()
        titles = [t["title"] for t in data["tasks"]]
        self.assertIn("Crit", titles)
        self.assertNotIn("Low", titles)

    def test_app_task_create_multi_assignee(self):
        # second employee
        u2 = User.objects.create_user(username="p2_emp2", password="pw", first_name="Two")
        e2 = Employee.objects.create(user=u2, role="employee", salary=0, active=True)
        c = self.c("admin")
        resp = c.post(reverse("clients:app_task_create"),
                      data=json.dumps({"title": "Multi", "assignees": [self.assignee.id, e2.id]}),
                      content_type="application/json")
        ids = resp.json()["ids"]
        self.assertEqual(len(ids), 2)
        assignees = set(Task.objects.filter(id__in=ids).values_list("assigned_to_id", flat=True))
        self.assertEqual(assignees, {self.assignee.id, e2.id})

    def test_app_task_edit_action(self):
        u2 = User.objects.create_user(username="p2_ed", password="pw", first_name="Ed")
        e2 = Employee.objects.create(user=u2, role="employee", salary=0, active=True)
        cat = TaskCategory.objects.create(name="EdCat")
        task = Task.objects.create(title="Old", created_by=self.users["admin"],
                                   assigned_to=self.assignee, priority="low")
        c = self.c("admin")
        c.post(reverse("clients:app_task_action", args=[task.pk]),
               data=json.dumps({"action": "edit", "title": "New title", "priority": "high",
                                "category_id": cat.id, "assigned_to": e2.id,
                                "due_date": "2027-05-05", "checklist": ["x", "y"],
                                "subscribers": [self.users["admin"].id]}),
               content_type="application/json")
        task.refresh_from_db()
        self.assertEqual(task.title, "New title")
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.category_id, cat.id)
        self.assertEqual(task.assigned_to_id, e2.id)
        self.assertEqual(task.due_date.isoformat(), "2027-05-05")
        self.assertEqual(task.checklist_items.count(), 2)
        self.assertEqual(task.subscribers.count(), 1)

    def test_app_task_category_create(self):
        resp = self.c("admin").post(reverse("clients:app_task_category_create"),
                                    data=json.dumps({"name": "Ops2", "color": "#111111"}),
                                    content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        self.assertTrue(TaskCategory.objects.filter(name="Ops2").exists())

    def test_app_task_scorecard(self):
        Task.objects.create(title="A", created_by=self.users["admin"], assigned_to=self.assignee, status="completed")
        Task.objects.create(title="B", created_by=self.users["admin"], assigned_to=self.assignee, status="pending")
        data = self.c("admin").get(reverse("clients:app_task_scorecard"), {"period": "month"}).json()
        self.assertEqual(data["period"], "month")
        row = next(r for r in data["scorecard"] if r["total"] >= 2)
        self.assertEqual(row["completed_pct"] + row["not_completed_pct"], 100)

    def test_web_create_multi_assignee(self):
        u2 = User.objects.create_user(username="p2_emp3", password="pw", first_name="Three")
        e2 = Employee.objects.create(user=u2, role="employee", salary=0, active=True)
        before = Task.objects.count()
        self.c("admin").post(reverse("clients:task_create"), {
            "title": "Web multi", "priority": "medium",
            "assigned_to": [str(self.assignee.id), str(e2.id)],
        })
        self.assertEqual(Task.objects.count(), before + 2)

    def test_app_links_and_favorite(self):
        link = Link.objects.create(title="AMFI", url="https://amfi.com",
                                   created_by=self.users["admin"])
        c = self.c("employee")
        data = c.get(reverse("clients:app_links")).json()
        self.assertTrue(any(l["title"] == "AMFI" for l in data["links"]))
        c.post(reverse("clients:app_link_favorite", args=[link.id]))
        self.assertTrue(LinkFavorite.objects.filter(user=self.users["employee"], link=link).exists())

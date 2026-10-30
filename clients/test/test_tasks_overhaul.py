"""The task module audit of 2026-10-29: every bug it found, pinned.

Each test reproduces one defect as it was reported, so it fails on the code
before the fix. Grouped by what breaks for whom.

Run: .venv/bin/python manage.py test clients.test.test_tasks_overhaul
"""
import json
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import (
    Client as CrmClient,
    Employee,
    Notification,
    NotificationPreference,
    RecurringTaskRule,
    Task,
    TaskCategory,
    TaskChecklistItem,
    TaskSubscriber,
)
from clients.services.tasks import generate_recurring, next_due_date


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user(username="ov_boss", password="pw", first_name="Boss")
        cls.boss_emp = Employee.objects.create(user=cls.boss, role="admin", salary=0, active=True)
        cls.people = []
        for name in ("mansi", "rohit", "amit"):
            u = User.objects.create_user(username=f"ov_{name}", password="pw", first_name=name.title())
            cls.people.append(Employee.objects.create(user=u, role="employee", salary=0, active=True))
        cls.watcher = User.objects.create_user(username="ov_watch", password="pw")
        Employee.objects.create(user=cls.watcher, role="employee", salary=0, active=True)

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def _group(self, title="Group job", **extra):
        self._client(self.boss).post(reverse("clients:task_create"), {
            "title": title, "priority": "medium",
            "assigned_to": [str(p.pk) for p in self.people], **extra,
        })
        return list(Task.objects.filter(title=title).order_by("pk"))

    def _app(self, user, task, payload):
        return self._client(user).post(reverse("clients:app_task_action", args=[task.pk]),
                                       data=json.dumps(payload), content_type="application/json")


class GroupTaskTests(_Base):
    """A task given to three people is three rows, but one task to everyone."""

    def test_phone_due_change_moves_every_assignee(self):
        tasks = self._group()
        self._app(self.boss, tasks[0], {"action": "due", "due_date": "2026-12-01", "due_time": "11:00"})
        self.assertEqual({str(t.due_date) for t in Task.objects.filter(title="Group job")}, {"2026-12-01"})

    def test_phone_priority_category_and_description_move_every_assignee(self):
        tasks = self._group()
        cat = TaskCategory.objects.create(name="Ops")
        self._app(self.boss, tasks[0], {"action": "priority", "priority": "critical"})
        self._app(self.boss, tasks[0], {"action": "category", "category_id": cat.pk})
        self._app(self.boss, tasks[0], {"action": "description", "description": "New brief"})
        rows = Task.objects.filter(title="Group job")
        self.assertEqual({(t.priority, t.category_id, t.description) for t in rows},
                         {("critical", cat.pk, "New brief")})

    def test_a_moved_deadline_rings_again_for_everyone(self):
        tasks = self._group(due_date=str(timezone.localdate()), due_time="09:00")
        Task.objects.filter(title="Group job").update(due_alarm_sent_at=timezone.now(),
                                                      reminded_same_day=True)
        self._client(self.boss).post(reverse("clients:task_set_due", args=[tasks[0].pk]),
                                     {"due_date": "2026-12-02", "due_time": "10:00"})
        for t in Task.objects.filter(title="Group job"):
            self.assertIsNone(t.due_alarm_sent_at, f"{t.assigned_to} will never ring")
            self.assertFalse(t.reminded_same_day)

    def test_calendar_drag_rearms_every_assignee(self):
        tasks = self._group(due_date=str(timezone.localdate()), due_time="09:00")
        Task.objects.filter(title="Group job").update(due_alarm_sent_at=timezone.now())
        self._client(self.boss).post(reverse("clients:task_reschedule", args=[tasks[0].pk]),
                                     data=json.dumps({"date": "2026-12-03"}),
                                     content_type="application/json")
        self.assertFalse(Task.objects.filter(title="Group job", due_alarm_sent_at__isnull=False).exists())

    def test_a_deadline_change_tells_every_assignee(self):
        tasks = self._group()
        Notification.objects.all().delete()
        self._client(self.boss).post(reverse("clients:task_set_due", args=[tasks[0].pk]),
                                     {"due_date": "2026-12-04", "due_time": "10:00"})
        told = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertEqual(told, {p.user.username for p in self.people})

    def test_restoring_a_deleted_group_brings_everyone_back(self):
        tasks = self._group()
        self._client(self.boss).post(reverse("clients:task_delete", args=[tasks[0].pk]))
        self._client(self.boss).post(reverse("clients:task_restore", args=[tasks[0].pk]))
        self.assertEqual(Task.objects.filter(title="Group job", is_deleted=False).count(), 3)

    def test_someone_put_in_loop_is_in_loop_for_the_whole_task(self):
        tasks = self._group()
        self._client(self.boss).post(reverse("clients:task_add_subscriber", args=[tasks[0].pk]),
                                     {"user": self.watcher.pk})
        self.assertEqual(TaskSubscriber.objects.filter(user=self.watcher).count(), 3)

    def test_a_watcher_hears_once_when_a_group_task_goes_overdue(self):
        tasks = self._group(due_date=str(timezone.localdate() - timedelta(days=1)),
                            subscribers=[str(self.watcher.pk)])
        Task.objects.filter(title="Group job").update(status="pending")
        Notification.objects.all().delete()
        call_command("tasks_mark_overdue")
        self.assertEqual(Notification.objects.filter(recipient=self.watcher,
                                                     title="Task overdue").count(), 1)
        for p in self.people:   # each assignee still hears about their own
            self.assertEqual(Notification.objects.filter(recipient=p.user,
                                                         title="Task overdue").count(), 1)


class SharedThreadTests(_Base):
    """Comments and files on a group task are one thread, seen by everyone on it."""

    def test_a_file_attached_when_assigning_reaches_every_assignee(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        with mock.patch("clients.services.google_drive.get_or_create_tasks_root", return_value="root"), \
             mock.patch("clients.services.google_drive.upload_file", return_value=("f1", "")):
            self._client(self.boss).post(reverse("clients:task_create"), {
                "title": "With brief", "priority": "medium",
                "assigned_to": [str(p.pk) for p in self.people],
                "voice_note": SimpleUploadedFile("brief.webm", b"x", "audio/webm")})
        rows = Task.objects.filter(title="With brief").order_by("pk")
        last = self.people[2].user
        page = self._client(last).get(reverse("clients:task_detail", args=[rows[2].pk])).content.decode()
        self.assertIn("brief.webm", page)
        app = json.loads(self._client(last).get(reverse("clients:app_task_detail", args=[rows[2].pk])).content)
        self.assertEqual([a["filename"] for a in app["attachments"]], ["brief.webm"])
        att_url = app["attachments"][0]["url"]
        with mock.patch("clients.services.google_drive.stream_file", return_value=(b"x", "audio/webm")):
            self.assertEqual(self._client(last).get(att_url).status_code, 200)

    def test_a_comment_on_a_group_task_reaches_everyone_and_shows_on_every_copy(self):
        rows = self._group()
        Notification.objects.all().delete()
        with mock.patch("clients.services.push.send_data_push_to_user"):
            self._client(self.boss).post(reverse("clients:task_add_comment", args=[rows[0].pk]),
                                         {"body": "Submit by 5pm please"})
        told = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertEqual(told, {p.user.username for p in self.people})
        page = self._client(self.people[2].user).get(
            reverse("clients:task_detail", args=[rows[2].pk])).content.decode()
        self.assertIn("Submit by 5pm please", page)

    def test_the_calendar_escapes_task_titles(self):
        # The calendar builds its cells as HTML; a title must go through esc().
        Task.objects.create(title="Cal", created_by=self.boss, due_date=timezone.localdate())
        html = self._client(self.boss).get(reverse("clients:task_all") + "?view=calendar").content.decode()
        self.assertIn("esc(e.title)", html)
        self.assertNotIn("+e.title+'</a>'", html)


class EditTests(_Base):
    def _solo(self, **kw):
        t = Task.objects.create(title="Solo", created_by=self.boss, assigned_to=self.people[0], **kw)
        for i, title in enumerate(("Call", "Email", "File")):
            TaskChecklistItem.objects.create(task=t, title=title, order=i, is_done=(title != "File"))
        return t

    def test_editing_on_the_phone_keeps_checklist_ticks(self):
        t = self._solo()
        # The app's Edit sheet always sends the checklist as plain titles.
        self._app(self.boss, t, {"action": "edit", "title": "Solo v2",
                                 "checklist": ["Call", "Email", "File", "Courier"]})
        done = dict(t.checklist_items.values_list("title", "is_done"))
        self.assertEqual(done, {"Call": True, "Email": True, "File": False, "Courier": False})
        self.assertEqual(list(t.checklist_items.values_list("title", flat=True)),
                         ["Call", "Email", "File", "Courier"])

    def test_dropping_a_checklist_line_removes_only_that_line(self):
        t = self._solo()
        self._app(self.boss, t, {"action": "edit", "checklist": ["Email", "File"]})
        self.assertEqual(dict(t.checklist_items.values_list("title", "is_done")),
                         {"Email": True, "File": False})

    def test_reassigning_through_edit_keeps_the_task_and_its_history(self):
        from clients.models import TaskComment
        t = self._solo()
        TaskComment.objects.create(task=t, author=self.boss, body="Client prefers mornings")
        resp = self._client(self.boss).post(reverse("clients:task_edit", args=[t.pk]), {
            "title": "Solo", "priority": "medium", "assigned_to": [str(self.people[1].pk)]})
        t.refresh_from_db()
        self.assertFalse(t.is_deleted, "the task was binned instead of reassigned")
        self.assertEqual(t.assigned_to, self.people[1])
        self.assertEqual(t.comments.count(), 1)
        self.assertEqual(Task.objects.filter(title="Solo").count(), 1)
        self.assertRedirects(resp, reverse("clients:task_detail", args=[t.pk]),
                             fetch_redirect_response=False)

    def test_taking_yourself_off_a_group_task_opens_a_live_copy(self):
        rows = Task.objects.bulk_create([
            Task(title="Pair", created_by=self.boss, assigned_to=p, assign_group="g1")
            for p in self.people[:2]])
        resp = self._client(self.boss).post(reverse("clients:task_edit", args=[rows[0].pk]), {
            "title": "Pair", "priority": "medium", "assigned_to": [str(self.people[1].pk)]})
        self.assertRedirects(resp, reverse("clients:task_detail", args=[rows[1].pk]),
                             fetch_redirect_response=False)

    def test_an_edit_without_a_due_time_keeps_the_time(self):
        t = self._solo(due_date=date(2026, 12, 1), due_time=time(15, 30))
        self._app(self.boss, t, {"action": "edit", "due_date": "2026-12-05"})
        t.refresh_from_db()
        self.assertEqual((t.due_date, t.due_time), (date(2026, 12, 5), time(15, 30)))


class RecurrenceTests(_Base):
    def _rule(self, freq, start, **kw):
        kw.setdefault("assigned_to", self.people[0])
        return RecurringTaskRule.objects.create(
            title="Repeat", frequency=freq, interval=1, created_by=self.boss,
            start_date=start, occurrences_created=1, last_generated_date=start, **kw)

    def test_a_monthly_task_on_the_31st_does_not_slide_to_the_28th(self):
        rule = self._rule("monthly", date(2026, 1, 31))
        self.assertEqual(next_due_date(rule, date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(next_due_date(rule, date(2026, 2, 28)), date(2026, 3, 31))
        self.assertEqual(next_due_date(rule, date(2026, 3, 31)), date(2026, 4, 30))

    def test_missed_days_are_not_backfilled_as_a_pile_of_overdue_tasks(self):
        today = timezone.localdate()
        rule = self._rule("daily", today - timedelta(days=12))
        generate_recurring(today=today)
        made = Task.objects.filter(recurring_rule=rule)
        self.assertEqual([t.due_date for t in made], [today])

    def test_a_repeating_task_for_three_people_repeats_for_three_people_as_one_task(self):
        tasks = self._group("Daily report", repeat_rule="daily",
                            due_date=str(timezone.localdate()))
        self.assertEqual(RecurringTaskRule.objects.count(), 1)
        generate_recurring(today=timezone.localdate() + timedelta(days=1))
        tomorrow = Task.objects.filter(title="Daily report", due_date=timezone.localdate() + timedelta(days=1))
        self.assertEqual({t.assigned_to_id for t in tomorrow}, {p.pk for p in self.people})
        self.assertEqual(len({t.assign_group for t in tomorrow}), 1)
        self.assertTrue(tomorrow[0].assign_group)
        # And deleting one day's task stops the series for everyone.
        self._client(self.boss).post(reverse("clients:task_delete", args=[tasks[0].pk]))
        self.assertFalse(RecurringTaskRule.objects.get().is_active)

    def test_editing_a_repeating_task_changes_the_next_one(self):
        self._client(self.boss).post(reverse("clients:task_create"), {
            "title": "Weekly MIS", "priority": "medium", "repeat_rule": "daily",
            "assigned_to": [str(self.people[0].pk)], "due_date": str(timezone.localdate()),
        })
        task = Task.objects.get(title="Weekly MIS")
        self._app(self.boss, task, {"action": "edit", "title": "Weekly MIS v2",
                                    "assignees": [self.people[1].pk], "priority": "high"})
        generate_recurring(today=timezone.localdate() + timedelta(days=1))
        nxt = Task.objects.get(recurring_rule__isnull=False, due_date=timezone.localdate() + timedelta(days=1))
        self.assertEqual((nxt.title, nxt.assigned_to, nxt.priority),
                         ("Weekly MIS v2", self.people[1], "high"))


class AcknowledgementTests(_Base):
    def test_a_task_you_gave_yourself_never_asks_you_to_acknowledge_it(self):
        me = self.people[0]
        self._client(me.user).post(reverse("clients:task_create"), {
            "title": "Note to self", "priority": "high", "assigned_to": [str(me.pk)]})
        self.assertIsNotNone(Task.objects.get(title="Note to self").acknowledged_at)

    def test_the_assignee_can_acknowledge_on_the_web(self):
        t = Task.objects.create(title="Ack me", created_by=self.boss, assigned_to=self.people[0],
                                priority="high")
        Notification.objects.all().delete()
        self._client(self.people[0].user).post(reverse("clients:task_acknowledge", args=[t.pk]))
        t.refresh_from_db()
        self.assertIsNotNone(t.acknowledged_at)
        self.assertTrue(Notification.objects.filter(recipient=self.boss).exists())
        html = self._client(self.people[0].user).get(reverse("clients:task_detail", args=[t.pk])).content.decode()
        self.assertNotIn('action="' + reverse("clients:task_acknowledge", args=[t.pk]), html)

    def test_only_the_assignee_sees_the_acknowledge_button(self):
        t = Task.objects.create(title="Ack me", created_by=self.boss, assigned_to=self.people[0])
        url = reverse("clients:task_acknowledge", args=[t.pk])
        self.assertIn(url, self._client(self.people[0].user).get(
            reverse("clients:task_detail", args=[t.pk])).content.decode())
        self.assertNotIn(url, self._client(self.boss).get(
            reverse("clients:task_detail", args=[t.pk])).content.decode())


class NotificationTests(_Base):
    def test_the_assigner_hears_when_the_task_is_done(self):
        t = Task.objects.create(title="Do it", created_by=self.boss, assigned_to=self.people[0])
        self._client(self.people[0].user).post(reverse("clients:task_set_status", args=[t.pk]),
                                               {"status": "completed"})
        self.assertTrue(Notification.objects.filter(recipient=self.boss, title="Task completed").exists())

    def test_the_assigner_hears_the_assignees_comment(self):
        t = Task.objects.create(title="Do it", created_by=self.boss, assigned_to=self.people[0])
        with mock.patch("clients.services.push.send_data_push_to_user"):
            self._client(self.people[0].user).post(reverse("clients:task_add_comment", args=[t.pk]),
                                                   {"body": "Need the PAN copy"})
        self.assertTrue(Notification.objects.filter(recipient=self.boss).exists())

    def test_turning_off_overdue_notices_turns_them_off(self):
        NotificationPreference.objects.create(user=self.people[0].user, notify_overdue=False)
        Task.objects.create(title="Late", created_by=self.boss, assigned_to=self.people[0],
                            due_date=timezone.localdate() - timedelta(days=1))
        call_command("tasks_mark_overdue")
        self.assertFalse(Notification.objects.filter(recipient=self.people[0].user,
                                                     title="Task overdue").exists())

    def test_escalation_never_writes_to_an_inactive_user(self):
        ghost = User.objects.create_user(username="ov_system", is_active=False)
        Task.objects.create(title="Old", created_by=ghost, assigned_to=self.people[0],
                            status="overdue", due_date=timezone.localdate() - timedelta(days=5))
        with mock.patch("django.utils.timezone.localtime",
                        return_value=timezone.make_aware(datetime.combine(timezone.localdate(), time(10)))):
            call_command("tasks_mark_overdue")
        self.assertFalse(Notification.objects.filter(recipient=ghost).exists())

    def test_a_deadline_just_before_midnight_still_rings(self):
        today = timezone.localdate()
        t = Task.objects.create(title="Late night", created_by=self.boss, assigned_to=self.people[0],
                                due_date=today - timedelta(days=1), due_time=time(23, 55))
        just_after = timezone.make_aware(datetime.combine(today, time(0, 3)))
        with mock.patch("clients.management.commands.tasks_ring_due.timezone.localtime",
                        return_value=just_after), \
             mock.patch("clients.services.push.send_data_push_to_user"):
            call_command("tasks_ring_due")
        t.refresh_from_db()
        self.assertIsNotNone(t.due_alarm_sent_at)


class PageTests(_Base):
    def test_detail_names_the_client_and_links_back_to_the_lead(self):
        client = CrmClient.objects.create(id=97001, name="Kiran Patil", phone="9876543210")
        t = Task.objects.create(title="Call back", created_by=self.boss, assigned_to=self.people[0],
                                client=client, source_kind="lead", source_id=42)
        html = self._client(self.boss).get(reverse("clients:task_detail", args=[t.pk])).content.decode()
        self.assertIn("Kiran Patil", html)
        self.assertIn('href="tel:9876543210"', html)
        self.assertIn(reverse("clients:lead_detail", args=[42]), html)
        # The phone gets the same link.
        data = self._client(self.boss).get(reverse("clients:app_task_detail", args=[t.pk])).json()
        self.assertEqual(data["source"], {"label": "Lead #42",
                                          "url": reverse("clients:lead_detail", args=[42])})

    def test_a_capped_list_says_it_is_capped(self):
        me = self.people[0]
        Task.objects.bulk_create(Task(title=f"T{i}", created_by=self.boss, assigned_to=me)
                                 for i in range(301))
        html = self._client(me.user).get(reverse("clients:task_my")).content.decode()
        self.assertIn("Showing the newest 300 tasks only", html)

    def test_a_pending_task_past_its_deadline_reads_overdue_before_the_cron(self):
        Task.objects.create(title="Missed it", created_by=self.boss, assigned_to=self.people[0],
                            due_date=timezone.localdate() - timedelta(days=1))
        html = self._client(self.people[0].user).get(reverse("clients:task_my")).content.decode()
        self.assertIn('tk-st-overdue">Overdue', html)

    def test_a_deleted_task_offers_no_edit_controls(self):
        t = Task.objects.create(title="Gone", created_by=self.boss, assigned_to=self.people[0],
                                is_deleted=True)
        html = self._client(self.boss).get(reverse("clients:task_detail", args=[t.pk])).content.decode()
        self.assertNotIn(reverse("clients:task_set_status", args=[t.pk]), html)

    def test_junk_ids_are_ignored_not_a_server_error(self):
        c = self._client(self.boss)
        resp = c.post(reverse("clients:task_bulk_status"), {"task_ids": ["abc", "1"], "status": "completed"})
        self.assertEqual(resp.status_code, 302)
        resp = c.post(reverse("clients:task_create"), {"title": "X", "subscribers": ["999999"],
                                                       "assigned_to": [str(self.people[0].pk)]})
        self.assertEqual(resp.status_code, 302)
        resp = c.post(reverse("clients:app_task_create"), data=json.dumps(
            {"title": "Y", "subscribers": [999999], "category_id": "abc"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_a_file_name_with_quotes_cannot_break_the_download_header(self):
        from clients.models import TaskAttachment
        t = Task.objects.create(title="Files", created_by=self.boss)
        att = TaskAttachment.objects.create(task=t, filename='a"b.pdf', drive_file_id="f1", mime="application/pdf")
        with mock.patch("clients.services.google_drive.stream_file", return_value=(b"x", "application/pdf")):
            resp = self._client(self.boss).get(reverse("clients:task_attachment_download", args=[att.pk]))
        self.assertNotIn('filename="a"b.pdf"', resp["Content-Disposition"])

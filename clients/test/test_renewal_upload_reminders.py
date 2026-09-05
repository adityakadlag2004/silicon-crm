"""Chasing the renewed policy into Drive, four days after the renewal.

The insurer issues the document three or four days after the renewal is
entered, so `Renewal.policy_doc_submitted` is blank on the day and nothing
asked for it again. `renewal_upload_reminders` raises one task for the
employee who entered it; ticking the box closes that task.

Run: .venv/bin/python manage.py test clients.test.test_renewal_upload_reminders -v 2
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from clients.models import Client, Employee, Renewal, Task


def _age(renewal, days):
    """Backdate a renewal's created_at — auto_now_add ignores what you pass."""
    Renewal.objects.filter(pk=renewal.pk).update(
        created_at=timezone.now() - timedelta(days=days))
    renewal.refresh_from_db()
    return renewal


class RenewalUploadReminderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        u = User.objects.create_user("ren_emp", password="pw")
        cls.emp = Employee.objects.create(user=u, role="employee", salary=0, active=True)
        other = User.objects.create_user("ren_other", password="pw")
        cls.other = Employee.objects.create(user=other, role="employee", salary=0, active=True)
        cls.client_row = Client.objects.create(name="RENEWAL CLIENT", mapped_to=cls.other)

    def _renewal(self, days_old=4, filed=False, employee=True):
        r = Renewal.objects.create(
            client=self.client_row, product_type=Renewal.PRODUCT_TYPE_HEALTH,
            renewal_date=timezone.localdate(), frequency=Renewal.FREQUENCY_YEARLY,
            employee=self.emp if employee else None,
            premium_amount=12000, policy_doc_submitted=filed,
        )
        return _age(r, days_old)

    def test_it_chases_the_employee_who_entered_it(self):
        r = self._renewal()
        call_command("renewal_upload_reminders")
        task = Task.objects.get(assign_group=f"renupl:{r.pk}")
        self.assertEqual(task.assigned_to, self.emp)          # not the mapped employee
        self.assertEqual(task.client, self.client_row)
        self.assertEqual(task.priority, Task.PRIORITY_MEDIUM)  # a filing nudge, not an alarm
        self.assertIsNotNone(task.due_time)                    # or tasks_ring_due skips it

    def test_it_is_quiet_before_the_fourth_day(self):
        self._renewal(days_old=2)
        call_command("renewal_upload_reminders")
        self.assertFalse(Task.objects.exists())

    def test_a_filed_renewal_is_never_chased(self):
        self._renewal(filed=True)
        call_command("renewal_upload_reminders")
        self.assertFalse(Task.objects.exists())

    def test_running_it_again_raises_nothing_further(self):
        self._renewal()
        call_command("renewal_upload_reminders")
        call_command("renewal_upload_reminders")
        self.assertEqual(Task.objects.count(), 1)

    def test_a_missed_morning_still_catches_the_renewal(self):
        r = self._renewal(days_old=6)
        call_command("renewal_upload_reminders")
        self.assertTrue(Task.objects.filter(assign_group=f"renupl:{r.pk}").exists())

    def test_an_old_unfiled_renewal_is_not_dredged_up(self):
        self._renewal(days_old=40)
        call_command("renewal_upload_reminders")
        self.assertFalse(Task.objects.exists())

    def test_with_no_entering_employee_it_falls_to_the_mapped_one(self):
        self._renewal(employee=False)
        call_command("renewal_upload_reminders")
        self.assertEqual(Task.objects.get().assigned_to, self.other)

    def test_ticking_the_box_closes_the_task(self):
        r = self._renewal()
        call_command("renewal_upload_reminders")
        r.policy_doc_submitted = True
        r.save()
        task = Task.objects.get(assign_group=f"renupl:{r.pk}")
        self.assertEqual(task.status, Task.STATUS_COMPLETED)
        self.assertIsNotNone(task.completed_at)

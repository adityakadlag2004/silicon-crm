import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from clients.models import CalendarEvent, Employee, NetBusinessEntry


User = get_user_model()


class SecurityHardeningTests(TestCase):
    def setUp(self):
        cache.clear()

    def _create_user_with_employee(self, username, role="employee", password="pass123"):
        user = User.objects.create_user(username=username, password=password)
        employee = Employee.objects.create(user=user, role=role)
        return user, employee

    def test_login_lockout_after_failed_attempts(self):
        user, _employee = self._create_user_with_employee("locked_user")

        url = reverse("clients:login")
        for _ in range(5):
            self.client.post(url, {"username": user.username, "password": "wrong"})

        response = self.client.post(url, {"username": user.username, "password": "pass123"}, follow=True)

        self.assertContains(response, "Too many failed login attempts")
        self.assertFalse(response.context["user"].is_authenticated)

    # Removed: test_employee_cannot_create_calendar_event_for_unassigned_prospect
    # Calling component (CallingList, Prospect, CallRecord) was deleted from the project.

    def test_manager_cannot_delete_other_manager_net_business_entry(self):
        user_a, _emp_a = self._create_user_with_employee("mgr_a", role="manager")
        user_b, _emp_b = self._create_user_with_employee("mgr_b", role="manager")

        own_entry = NetBusinessEntry.objects.create(
            entry_type="sale",
            amount=1000,
            date=timezone.now().date(),
            created_by=user_a,
        )
        foreign_entry = NetBusinessEntry.objects.create(
            entry_type="sale",
            amount=2000,
            date=timezone.now().date(),
            created_by=user_b,
        )

        self.client.login(username="mgr_a", password="pass123")
        response = self.client.post(
            reverse("clients:net_business"),
            {"action": "delete", "entry_id": str(foreign_entry.id)},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(NetBusinessEntry.objects.filter(id=foreign_entry.id).exists())
        self.assertTrue(NetBusinessEntry.objects.filter(id=own_entry.id).exists())

    # Removed: test_upload_list_rejects_disallowed_extension /
    # test_upload_list_rejects_oversized_file
    # The clients:upload_list URL was part of the calling component, now removed.
    # If you add a different file-upload feature later, reuse the SimpleUploadedFile
    # patterns above (still imported at the top).

    def test_calendar_create_event_is_throttled(self):
        user, _employee = self._create_user_with_employee("rate_user", role="employee")
        self.client.login(username=user.username, password="pass123")

        payload = {
            "title": "Rate test",
            "type": "task",
            "scheduled_time": timezone.now().isoformat(),
        }

        for _ in range(30):
            response = self.client.post(
                reverse("clients:create_calendar_event"),
                data=json.dumps(payload),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        throttled = self.client.post(
            reverse("clients:create_calendar_event"),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(throttled.status_code, 429)

    def test_employee_cannot_bulk_reassign(self):
        """Plain employees must be redirected away from the bulk reassign view."""
        user, _emp = self._create_user_with_employee("plain_employee", role="employee")
        self.client.login(username="plain_employee", password="pass123")
        response = self.client.post(reverse("clients:bulk_reassign"), follow=True)
        self.assertRedirects(response, reverse("clients:employee_dashboard"))

    def test_manager_can_access_bulk_reassign(self):
        """Managers should be allowed through the bulk reassign view."""
        user, _emp = self._create_user_with_employee("mgr_user", role="manager")
        self.client.login(username="mgr_user", password="pass123")
        response = self.client.get(reverse("clients:bulk_reassign"))
        self.assertEqual(response.status_code, 200)

"""Send reminders for everything due on the common calendar.

Runs every minute via CRONJOBS. Two reminder sources:

1. Call follow-ups — creates an in-app Notification with `_skip_push` set,
   then sends a high-priority data-only push (kind=followup_alarm) that the
   Android app turns into a full-screen ringing alarm — client name as the
   title, the note as the body, with Call / Done / Snooze actions.
   (The app also schedules the same alarm locally via AlarmManager, so the
   reminder rings even with no connectivity; the device dedupes by id.)

2. Calendar events — creates a regular Notification at the event's start
   time linking to the calendar.

Each row carries a `reminded` flag so a reminder fires exactly once.
Lead and claim follow-ups are NOT handled here: a follow-up is a Task now
(services/followups.py), so it rings through tasks_ring_due like every other
task instead of arriving as a tray notification nobody sees.
"""
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from clients.models import CalendarEvent, CallFollowUp, Notification
from clients.services.push import send_data_push_to_user


class Command(BaseCommand):
    help = "Send push/in-app reminders for due call follow-ups and calendar events."

    def handle(self, *args, **options):
        now = timezone.now()
        sent = self._call_followups(now) + self._events(now)
        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder(s)."))

    def _call_followups(self, now):
        due = CallFollowUp.objects.filter(
            status=CallFollowUp.STATUS_PENDING,
            reminded=False,
            scheduled_at__lte=now,
        ).select_related("employee__user", "client")

        sent = 0
        for fu in due:
            if not fu.employee.user_id:
                fu.reminded = True
                fu.save(update_fields=["reminded"])
                continue
            who = fu.client.name if fu.client_id else fu.phone
            note = fu.note or f"You scheduled a follow-up call with {who}. Tap to dial."
            notification = Notification(
                recipient=fu.employee.user,
                title=f"Call follow-up: {who}",
                body=note,
                link=f"tel:{fu.phone}",
            )
            notification._skip_push = True  # the alarm push below replaces the plain mirror
            notification.save()
            send_data_push_to_user(fu.employee.user, {
                "kind": "followup_alarm",
                "followup_id": fu.id,
                "client": who,
                "note": fu.note,
                "phone": fu.phone,
            })
            fu.reminded = True
            fu.save(update_fields=["reminded"])
            sent += 1
        return sent

    def _events(self, now):
        due = CalendarEvent.objects.filter(
            status="pending",
            reminded=False,
            scheduled_time__lte=now,
        ).select_related("employee__user")

        sent = 0
        for ev in due:
            if not ev.employee.user_id:
                ev.reminded = True
                ev.save(update_fields=["reminded"])
                continue
            Notification.objects.create(
                recipient=ev.employee.user,
                title=f"Event: {ev.title}",
                body=ev.notes or "Calendar event is starting now.",
                link=reverse("clients:employee_calendar_page"),
            )
            ev.reminded = True
            ev.save(update_fields=["reminded"])
            sent += 1
        return sent

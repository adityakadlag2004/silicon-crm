"""Send reminders for due call follow-ups.

Runs every minute via CRONJOBS. For each due follow-up it:
  1. Creates an in-app Notification (for the app's Notifications screen and
     the web bell) with `_skip_push` set, so the generic FCM mirror stays
     silent, and
  2. Sends a high-priority data-only push (kind=followup_alarm) that the
     Android app turns into a full-screen ringing alarm — client name as the
     title, the note as the body, with Call / Done / Snooze actions.

The app also schedules the same alarm locally (AlarmManager) when a follow-up
is created or synced, so the reminder rings even with no connectivity; the
device dedupes the two triggers by follow-up id.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import CallFollowUp, Notification
from clients.services.push import send_data_push_to_user


class Command(BaseCommand):
    help = "Send push/in-app reminders for call follow-ups that are due."

    def handle(self, *args, **options):
        due = CallFollowUp.objects.filter(
            status=CallFollowUp.STATUS_PENDING,
            reminded=False,
            scheduled_at__lte=timezone.now(),
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

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} follow-up reminder(s)."))

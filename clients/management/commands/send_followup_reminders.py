"""Send reminders for everything due on the common calendar.

Runs every minute via CRONJOBS. Covers the three reminder sources:

1. Call follow-ups — creates an in-app Notification with `_skip_push` set,
   then sends a high-priority data-only push (kind=followup_alarm) that the
   Android app turns into a full-screen ringing alarm — client name as the
   title, the note as the body, with Call / Done / Snooze actions.
   (The app also schedules the same alarm locally via AlarmManager, so the
   reminder rings even with no connectivity; the device dedupes by id.)

2. Lead follow-ups — creates a regular Notification (web bell + app
   Notifications screen + standard FCM mirror) linking to the lead.

3. Calendar events — creates a regular Notification at the event's start
   time linking to the calendar.

Each row carries a `reminded` flag so a reminder fires exactly once.
Tasks are NOT handled here — they have their own reminder pipeline
(tasks_ring_due / tasks_send_reminders).
"""
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from clients.models import CalendarEvent, CallFollowUp, ClaimReminder, LeadFollowUp, Notification
from clients.services.push import send_data_push_to_user


class Command(BaseCommand):
    help = "Send push/in-app reminders for due call/lead follow-ups and calendar events."

    def handle(self, *args, **options):
        now = timezone.now()
        sent = (self._call_followups(now) + self._lead_followups(now)
                + self._events(now) + self._claim_reminders(now))
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

    def _lead_followups(self, now):
        due = LeadFollowUp.objects.filter(
            status="pending",
            reminded=False,
            scheduled_time__lte=now,
        ).select_related("assigned_to__user", "lead")

        sent = 0
        for fu in due:
            if not fu.assigned_to.user_id:
                fu.reminded = True
                fu.save(update_fields=["reminded"])
                continue
            Notification.objects.create(
                recipient=fu.assigned_to.user,
                title=f"Lead follow-up: {fu.lead.customer_name}",
                body=fu.note or "Scheduled lead follow-up is due.",
                link=reverse("clients:lead_detail", args=[fu.lead_id]),
            )
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

    def _claim_reminders(self, now):
        """Push a due claim follow-up to the handler's phone. A regular
        Notification mirrors to FCM via signals.push_on_notification, so no
        new push wiring is needed."""
        due = ClaimReminder.objects.filter(
            status=ClaimReminder.STATUS_PENDING,
            reminded=False,
            scheduled_at__lte=now,
        ).select_related("employee__user", "claim__policy__client")

        sent = 0
        for r in due:
            if not (r.employee and r.employee.user_id):
                r.reminded = True
                r.save(update_fields=["reminded"])
                continue
            client = r.claim.policy.client
            Notification.objects.create(
                recipient=r.employee.user,
                title=f"Claim follow-up: {client.name}",
                body=r.note or f"Follow up on the claim for {r.claim.policy.policy_number}.",
                link=f"/clients/claims/{r.claim_id}/",
            )
            r.reminded = True
            r.save(update_fields=["reminded"])
            sent += 1
        return sent

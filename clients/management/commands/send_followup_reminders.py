"""Send reminders for due call follow-ups.

Runs every minute via CRONJOBS. Creates an in-app Notification for each due
follow-up — the Notification post_save signal mirrors it to the employee's
phone as an FCM push. The link is a tel: URI, so tapping the notification in
the Android app opens the dialer with the number ready.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import CallFollowUp, Notification


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
            Notification.objects.create(
                recipient=fu.employee.user,
                title=f"Call follow-up: {who}",
                body=fu.note or f"You scheduled a follow-up call with {who}. Tap to dial.",
                link=f"tel:{fu.phone}",
            )
            fu.reminded = True
            fu.save(update_fields=["reminded"])
            sent += 1

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} follow-up reminder(s)."))

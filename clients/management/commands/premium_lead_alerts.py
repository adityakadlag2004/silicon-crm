"""Premium leads nobody is working: ring the owner.

Runs daily (CRONJOBS 9:10). A Premium lead (`Lead.VALUE_TIERS`) that has gone
`PREMIUM_QUIET_DAYS` without a stage move, a remark or any follow-up activity
gets one HIGH-priority follow-up task for its owner, due 10:00 today — so
`tasks_ring_due` rings it at the due minute and HIGH re-rings every four hours
until it is acknowledged. That is deliberately louder than an ordinary
follow-up: there are few premium leads, and losing one quietly is expensive.

No dedup key is needed: the task is itself an open follow-up on the lead, and
closing it counts as activity, so the same lead cannot come round again for
another week (`services.leads.quiet_premium_leads`).
"""
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from clients.models import Task
from clients.services import followups
from clients.services import leads as lead_service
from clients.templatetags.custom_filters import inr

DUE_TIME = time(10, 0)


class Command(BaseCommand):
    help = "High-priority follow-up for every premium lead untouched for a week."

    def handle(self, *args, **opts):
        now = timezone.now()
        due = timezone.make_aware(datetime.combine(timezone.localdate(), DUE_TIME))
        # Run by hand after 10, it still has to ring — a few minutes out.
        due = max(due, now + timedelta(minutes=5))

        made = 0
        for lead in lead_service.quiet_premium_leads():
            idle = (now - lead.stage_changed_at).days
            followups.schedule(
                followups.LEAD, lead, due,
                note=(f"Premium lead · ₹{inr(lead.total_value)} a year · at "
                      f"{lead.get_stage_display()} for {idle} days, nothing logged this "
                      f"week. Call them and book the next step."),
                priority=Task.PRIORITY_HIGH,
            )
            made += 1
        self.stdout.write(self.style.SUCCESS(f"Premium lead alerts: {made} task(s) raised."))

"""Cron entry points that run more than one command in a single process.

django-crontab starts a fresh Python + Django for every CRONJOBS line. On the
one-vCPU droplet that boot is a couple of seconds of the only CPU, and two
every-minute lines meant two of them at the top of every minute, with web
requests queued behind both.
"""
import logging

from django.core.management import call_command

log = logging.getLogger(__name__)

EVERY_MINUTE = ("send_followup_reminders", "tasks_ring_due")


def every_minute():
    # One failing job must not stop the other — each one rings phones.
    for name in EVERY_MINUTE:
        try:
            call_command(name)
        except Exception:
            log.exception("cron %s failed", name)

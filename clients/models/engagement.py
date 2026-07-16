"""Calendar events, message templates/logs, notifications."""

import logging
import re

from django.conf import settings
from django.db import models
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


# Compiled once at module load — used by MessageTemplate.render() for safe variable substitution.
_TEMPLATE_VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')


class CalendarEvent(models.Model):
    EVENT_TYPES = [
        ("call_followup", "Call Follow-up"),
        ("meeting", "Meeting"),
        ("task", "Task"),
        ("reminder", "Reminder"),
    ]

    employee = models.ForeignKey("Employee", on_delete=models.CASCADE, related_name="calendar_events")
    client = models.ForeignKey("clients.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="calendar_events")
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=EVENT_TYPES, default="task")
    scheduled_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    reminder_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("completed", "Completed"),
            ("rescheduled", "Rescheduled"),
            ("skipped", "Skipped")
        ],
        default="pending",
        db_index=True,
    )
    # set once send_followup_reminders has notified the owner at start time
    reminded = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.employee})"


class MessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    content = models.TextField(help_text="Use any placeholders from the Client model like {{ name }}, {{ phone }}, {{ sip_amount }} etc.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def render(self, obj, extra_context=None):
        """
        Render the message with any placeholders present in 'content'.
        Automatically maps object attributes (e.g. from Client).
        """
        # Convert model instance to dict of all attributes
        context_data = {}

        # Add all field names + values from model (safe reflection)
        for field in obj._meta.get_fields():
            try:
                val = getattr(obj, field.name, "")
                # handle related fields (like mapped_to.user.username)
                if hasattr(val, "username"):
                    val = val.username
                context_data[field.name] = val
            except Exception:
                continue

        # Merge any extra values
        if extra_context:
            context_data.update(extra_context)

        # Replaces {{ variable }} patterns only — no tag execution, prevents injection.
        try:
            rendered = _TEMPLATE_VAR_RE.sub(
                lambda m: str(context_data.get(m.group(1).strip(), m.group(0))),
                self.content,
            )
            return strip_tags(rendered).strip()
        except Exception as e:
            logger.error("MessageTemplate render error: %s", e)
            return self.content


class MessageLog(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    template = models.ForeignKey(MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    client = models.ForeignKey('Client', null=True, blank=True, on_delete=models.SET_NULL)
    recipient_phone = models.CharField(max_length=32)
    message_text = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    error = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Message to {self.recipient_phone} [{self.status}]"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    related_sale = models.ForeignKey(
        "Sale", null=True, blank=True, on_delete=models.CASCADE
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"], name="notif_recip_read_idx"),
        ]

    def __str__(self):
        return f"{self.title} -> {self.recipient}"

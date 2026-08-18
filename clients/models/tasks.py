"""Task Management: tasks and their satellite models."""

from datetime import time as datetime_time

from django.conf import settings
from django.db import models
from django.utils import timezone


# ═══════════════════════════════════════════════════════════════════════════
#  TASK MANAGEMENT MODULE  (replaces the external "Automate Tasks" tool)
#
#  Reuses CRM auth (Employee/User), the Notification pipeline (creating a
#  Notification auto-pushes to FCM via signals.push_on_notification), and the
#  Google Drive service for attachments/voice notes. Roles: admin = full
#  management, manager = team-wide, employee = own/delegated/subscribed.
# ═══════════════════════════════════════════════════════════════════════════


class TaskCategory(models.Model):
    """Admin-managed category for tasks (Sales, Operations, HR, …)."""

    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=7, default="#E5B740",
                             help_text="Hex color, e.g. #E5B740.")
    icon = models.CharField(max_length=40, default="bi-folder-fill",
                            help_text="Bootstrap icon class, e.g. 'bi-briefcase-fill'.")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Task category"
        verbose_name_plural = "Task categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Task(models.Model):
    """A unit of work assigned to an Employee, tracked from creation to done."""

    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_CRITICAL = "critical"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_MEDIUM, "Medium"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_CRITICAL, "Critical"),
    ]

    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_OVERDUE = "overdue"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_OVERDUE, "Overdue"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    # Statuses that are still "live" — eligible to flip to Overdue.
    OPEN_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_OVERDUE)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(TaskCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="tasks")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES,
                                default=PRIORITY_MEDIUM)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES,
                              default=STATUS_PENDING, db_index=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="tasks_created")
    assigned_to = models.ForeignKey("Employee", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="tasks_assigned")
    # Optional client this task is about — surfaces the task on the client's
    # profile and gives the assignee a tap-to-call number on the task.
    client = models.ForeignKey("Client", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="tasks")

    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Accountability: the assignee taps "Acknowledge" (or changes status) to
    # confirm they've seen the task. tasks_ring_due re-rings unacknowledged
    # high/critical tasks every few hours (ack_last_rung_at is the marker).
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    ack_last_rung_at = models.DateTimeField(null=True, blank=True)
    # Exact due-time ring dispatched by tasks_ring_due (cleared on due change).
    due_alarm_sent_at = models.DateTimeField(null=True, blank=True)

    # "Don't ring": assign the task after hours without the phone going off
    # like an alarm clock. The assignee still gets the task and a plain tray
    # notification — only the ringing is suppressed, on every path (the
    # assignment/comment push, the due-time ring, and the app's on-device
    # alarm, which is starved by withholding due_at_ms).
    silent = models.BooleanField(
        default=False,
        help_text="Notify quietly — no ringing alarm on the phone.",
    )

    # One assignment to several people = one Task row per person (each tracks
    # its own status/acknowledgement) sharing an assign_group, so the creator's
    # Delegated list can show them collapsed as "Mansi +4".
    assign_group = models.CharField(max_length=32, blank=True, db_index=True)

    # A follow-up IS a task (services/followups.py) — there is no separate
    # follow-up record. These point back at whatever the follow-up was raised
    # on ("lead"/42), so that record's page can list its follow-ups and closing
    # it can cancel them. Blank on an ordinary hand-made task.
    source_kind = models.CharField(max_length=20, blank=True)
    source_id = models.PositiveIntegerField(null=True, blank=True)

    # Soft delete → recycle bin.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    # Recurrence: repeat_rule mirrors the parent rule's frequency for display;
    # `recurring_rule` links generated instances back to their series.
    repeat_rule = models.CharField(
        max_length=20, blank=True,
        help_text="Recurrence frequency shown on the task: '', daily, weekly, monthly, custom.",
    )
    recurring_rule = models.ForeignKey(
        "RecurringTaskRule", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="instances",
    )

    # Reminder dispatch flags (set once by tasks_send_reminders so we don't
    # re-notify the same task).
    reminded_day_before = models.BooleanField(default=False)
    reminded_same_day = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assigned_to", "status"], name="task_assignee_status_idx"),
            models.Index(fields=["status", "due_date"], name="task_status_due_idx"),
            models.Index(fields=["is_deleted"], name="task_deleted_idx"),
            models.Index(fields=["source_kind", "source_id"], name="task_source_idx"),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"

    @property
    def due_at(self):
        """Aware datetime for the deadline (end-of-day if no time), or None."""
        if not self.due_date:
            return None
        from datetime import datetime as _dt
        naive = _dt.combine(self.due_date, self.due_time or datetime_time(23, 59))
        return timezone.make_aware(naive, timezone.get_current_timezone())

    @property
    def is_overdue(self):
        """True when the deadline has passed and the task is still open."""
        due = self.due_at
        return bool(due and self.status in self.OPEN_STATUSES and due < timezone.now())

    @property
    def checklist_percent(self):
        """Whole-number completion % of the checklist (0 when no items)."""
        items = self.checklist_items.all()
        total = len(items)
        if not total:
            return 0
        done = sum(1 for i in items if i.is_done)
        return round(done * 100 / total)

    def mark_completed(self, by_user=None):
        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])


class TaskSubscriber(models.Model):
    """An "In Loop" user — informed of progress but not responsible."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subscribers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="task_subscriptions")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("task", "user")

    def __str__(self):
        return f"{self.user} ⊂ {self.task_id}"


class TaskChecklistItem(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist_items")
    title = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on {self.task_id} by {self.author}"


class TaskAttachment(models.Model):
    """A file or voice note stored in Google Drive; served via a proxy view."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=255)
    mime = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    drive_file_id = models.CharField(max_length=128)
    drive_view_link = models.URLField(blank=True)
    is_voice_note = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.filename


class TaskActivity(models.Model):
    """Append-only audit trail for every meaningful change to a task."""

    CREATED = "created"
    ASSIGNED = "assigned"
    SUBSCRIBER_ADDED = "subscriber_added"
    PRIORITY_CHANGED = "priority_changed"
    CATEGORY_CHANGED = "category_changed"
    DESCRIPTION_UPDATED = "description_updated"
    CHECKLIST_UPDATED = "checklist_updated"
    ATTACHMENT_UPLOADED = "attachment_uploaded"
    VOICENOTE_UPLOADED = "voicenote_uploaded"
    COMMENT_ADDED = "comment_added"
    DUE_CHANGED = "due_changed"
    STATUS_CHANGED = "status_changed"
    COMPLETED = "completed"
    DELETED = "deleted"
    RESTORED = "restored"
    ACKNOWLEDGED = "acknowledged"
    ACTION_CHOICES = [
        (CREATED, "Task Created"),
        (ASSIGNED, "Task Assigned"),
        (SUBSCRIBER_ADDED, "Subscriber Added"),
        (PRIORITY_CHANGED, "Priority Changed"),
        (CATEGORY_CHANGED, "Category Changed"),
        (DESCRIPTION_UPDATED, "Description Updated"),
        (CHECKLIST_UPDATED, "Checklist Updated"),
        (ATTACHMENT_UPLOADED, "Attachment Uploaded"),
        (VOICENOTE_UPLOADED, "Voice Note Uploaded"),
        (COMMENT_ADDED, "Comment Added"),
        (DUE_CHANGED, "Due Date Changed"),
        (STATUS_CHANGED, "Status Changed"),
        (COMPLETED, "Completed"),
        (DELETED, "Deleted"),
        (RESTORED, "Restored"),
        (ACKNOWLEDGED, "Acknowledged"),
    ]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=25, choices=ACTION_CHOICES, db_index=True)
    detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Task activities"

    def __str__(self):
        return f"{self.get_action_display()} on {self.task_id}"


class TaskTemplate(models.Model):
    """Reusable task blueprint ("New client onboarding", "Month-end closing"):
    picking one in the Assign sheet prefills title, description, priority,
    category and checklist, instead of retyping the same procedure each time.
    Created by admins/managers ("Save as template" in the Assign sheet)."""

    name = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=10, choices=Task.PRIORITY_CHOICES,
                                default=Task.PRIORITY_MEDIUM)
    category = models.ForeignKey(TaskCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    checklist = models.TextField(blank=True, default="",
                                 help_text="One checklist item per line.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def checklist_items(self):
        return [line.strip() for line in (self.checklist or "").splitlines() if line.strip()]


class RecurringTaskRule(models.Model):
    """A template + schedule that spawns Task instances (daily/weekly/monthly).

    The first instance is created immediately when a recurring task is assigned;
    subsequent ones are generated by the ``tasks_generate_recurring`` cron.
    """

    FREQ_DAILY = "daily"
    FREQ_WEEKLY = "weekly"
    FREQ_MONTHLY = "monthly"
    FREQ_CUSTOM = "custom"
    FREQ_CHOICES = [
        (FREQ_DAILY, "Daily"),
        (FREQ_WEEKLY, "Weekly"),
        (FREQ_MONTHLY, "Monthly"),
        (FREQ_CUSTOM, "Custom"),
    ]

    END_NEVER = "never"
    END_ON = "on_date"
    END_AFTER = "after_n"
    END_CHOICES = [
        (END_NEVER, "Never"),
        (END_ON, "On date"),
        (END_AFTER, "After N occurrences"),
    ]

    # ── Template for each generated task ──
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(TaskCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    priority = models.CharField(max_length=10, choices=Task.PRIORITY_CHOICES,
                                default=Task.PRIORITY_MEDIUM)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    assigned_to = models.ForeignKey("Employee", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    due_time = models.TimeField(null=True, blank=True)
    checklist_template = models.TextField(
        blank=True, help_text="One checklist item per line; copied onto each instance.")
    subscriber_ids = models.CharField(
        max_length=255, blank=True, help_text="CSV of User ids added In-Loop to each instance.")

    # ── Schedule ──
    frequency = models.CharField(max_length=10, choices=FREQ_CHOICES, default=FREQ_DAILY)
    interval = models.PositiveIntegerField(default=1, help_text="Every N days/weeks/months.")
    weekdays = models.CharField(
        max_length=20, blank=True,
        help_text="For weekly/custom: CSV weekday numbers, 0=Mon … 6=Sun.")
    start_date = models.DateField()
    end_type = models.CharField(max_length=10, choices=END_CHOICES, default=END_NEVER)
    end_date = models.DateField(null=True, blank=True)
    max_occurrences = models.PositiveIntegerField(null=True, blank=True)

    # ── State ──
    occurrences_created = models.PositiveIntegerField(default=0)
    last_generated_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_frequency_display()} · {self.title}"

    def weekday_list(self):
        out = []
        for part in (self.weekdays or "").split(","):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 6:
                out.append(int(part))
        return out

    def subscriber_id_list(self):
        out = []
        for part in (self.subscriber_ids or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out


class TaskReminderSetting(models.Model):
    """Singleton: admin-configured default reminder rules for tasks."""

    remind_day_before = models.BooleanField(default=True)
    remind_same_day = models.BooleanField(default=True)
    same_day_hour = models.PositiveSmallIntegerField(
        default=9, help_text="Hour (0-23) to send the same-day / day-before reminder.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Task reminder settings"
        verbose_name_plural = "Task reminder settings"

    def __str__(self):
        return "Task reminder settings"

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class NotificationPreference(models.Model):
    """Per-user toggles for which task notifications a user receives.

    Absence of a row means "all on" — the default. Field names match the event
    keys used by ``services.tasks.user_wants``.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="task_notification_pref")
    notify_assigned = models.BooleanField(default=True)
    notify_status_changed = models.BooleanField(default=True)
    notify_due_changed = models.BooleanField(default=True)
    notify_priority_changed = models.BooleanField(default=True)
    notify_comment_added = models.BooleanField(default=True)
    notify_checklist_updated = models.BooleanField(default=True)
    notify_attachment_added = models.BooleanField(default=True)
    notify_voicenote_added = models.BooleanField(default=True)
    notify_completed = models.BooleanField(default=True)
    notify_subscriber_added = models.BooleanField(default=True)
    notify_overdue = models.BooleanField(default=True)
    notify_recurring_created = models.BooleanField(default=True)

    def __str__(self):
        return f"Notification prefs for {self.user}"


class SavedTaskFilter(models.Model):
    """A named, per-user shortcut that stores a task-list query string."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="saved_task_filters")
    name = models.CharField(max_length=80)
    query_string = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("user", "name")

    def __str__(self):
        return self.name

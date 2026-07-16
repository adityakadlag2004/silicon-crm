"""Call tracking: settings, log entries, follow-ups, device status, push devices."""

from datetime import time as datetime_time

from django.conf import settings
from django.db import models
from .hr import Employee


class CallTrackingSettings(models.Model):
    """Singleton: admin-configured windows for call tracking and the follow-up
    popup. These are now independent — e.g. don't count calls on Sundays but
    still show the follow-up popup every day.

    Days are stored as comma-separated weekday numbers, 0=Mon … 6=Sun.
    """

    # ── Call tracking / analytics window ──
    enabled = models.BooleanField(default=True)
    work_start = models.TimeField(default=datetime_time(10, 0))
    work_end = models.TimeField(default=datetime_time(18, 0))
    work_days = models.CharField(
        max_length=20, default="0,1,2,3,4,5",
        help_text="Weekdays counted in analytics. 0=Mon … 6=Sun. Default Mon–Sat.",
    )

    # ── Follow-up popup window (independent of tracking) ──
    # 24×7 by default (owner decision 2026-07-14: a missed follow-up prompt is
    # worse than a late-night popup). Admin can still narrow it in App Settings.
    popup_enabled = models.BooleanField(default=True)
    popup_start = models.TimeField(default=datetime_time(0, 0))
    popup_end = models.TimeField(default=datetime_time(23, 59))
    popup_days = models.CharField(
        max_length=20, default="0,1,2,3,4,5,6",
        help_text="Weekdays the post-call popup appears. Default every day.",
    )
    # Which quick-timing chips the popup shows, comma-separated keys from
    # views.calls.FOLLOWUP_CATALOG in display order. Admin edits this from the
    # app Settings screen; the popup reads it via /api/calls/config/.
    popup_choices = models.CharField(
        max_length=250,
        default="10m,15m,30m,1h,2h,3h,4h,1d,5d,10d,1w,2w,1mo,2mo",
        help_text="Comma-separated quick-chip keys shown on the post-call popup.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Call Tracking Settings"
        verbose_name_plural = "Call Tracking Settings"

    def __str__(self):
        return f"Call tracking {self.work_start:%H:%M}–{self.work_end:%H:%M} ({'on' if self.enabled else 'off'})"

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @staticmethod
    def _parse_days(raw):
        out = []
        for part in (raw or "").split(","):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 6:
                out.append(int(part))
        return out

    def work_day_list(self):
        return self._parse_days(self.work_days)

    def popup_day_list(self):
        return self._parse_days(self.popup_days)

    def popup_choice_list(self):
        """Enabled quick-chip keys in display order (unvalidated — the view
        intersects with its catalog so stale keys degrade gracefully)."""
        return [p.strip() for p in (self.popup_choices or "").split(",") if p.strip()]

    @staticmethod
    def _to_django_week_days(day_list):
        """Map our 0=Mon…6=Sun to Django __week_day (1=Sun…7=Sat)."""
        # Mon(0)->2, Tue(1)->3, … Sat(5)->7, Sun(6)->1
        return [1 if d == 6 else d + 2 for d in day_list]

    def work_week_days_django(self):
        return self._to_django_week_days(self.work_day_list())

    def filter_work_window(self, qs, field="started_at"):
        """Restrict a CallLogEntry queryset to the tracking window
        (work hours + work days), evaluated in the project timezone."""
        return qs.filter(**{
            f"{field}__time__gte": self.work_start,
            f"{field}__time__lt": self.work_end,
            f"{field}__week_day__in": self.work_week_days_django(),
        })


class CallLogEntry(models.Model):
    """One phone call observed on an employee's device (synced by the app)."""

    DIRECTION_INCOMING = "incoming"
    DIRECTION_OUTGOING = "outgoing"
    DIRECTION_CHOICES = [
        (DIRECTION_INCOMING, "Incoming"),
        (DIRECTION_OUTGOING, "Outgoing"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="call_logs")
    phone = models.CharField(max_length=32, db_index=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    connected = models.BooleanField(default=False)
    duration_seconds = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(db_index=True)
    client = models.ForeignKey(
        "Client", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="call_logs", help_text="Auto-matched by phone number.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Dedup: the app may re-sync the same call.
        unique_together = [("employee", "phone", "started_at")]
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["employee", "-started_at"], name="call_emp_time_idx"),
        ]

    def __str__(self):
        return f"{self.employee} {self.direction} {self.phone} ({self.duration_seconds}s)"


class CallFollowUp(models.Model):
    """Follow-up on a phone call, scheduled from the post-call popup.
    A reminder push is sent at `scheduled_at`; tapping it dials the number."""

    STATUS_PENDING = "pending"
    STATUS_DONE = "done"
    STATUS_DISMISSED = "dismissed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_DONE, "Done"),
        (STATUS_DISMISSED, "Dismissed"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="call_followups")
    phone = models.CharField(max_length=32)
    client = models.ForeignKey(
        "Client", null=True, blank=True, on_delete=models.SET_NULL, related_name="call_followups"
    )
    scheduled_at = models.DateTimeField(db_index=True)
    note = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    reminded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "scheduled_at"]

    def __str__(self):
        return f"Follow-up {self.phone} @ {self.scheduled_at:%d-%b %H:%M} ({self.status})"


class AppDeviceStatus(models.Model):
    """Latest app/permission state per user, reported by the Android app on
    every launch. Lets admins see who hasn't granted call-tracking permissions
    (Call Analytics page shows the roster)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="app_device_status"
    )
    calls_granted = models.BooleanField(default=False)      # READ_PHONE_STATE + READ_CALL_LOG
    overlay_granted = models.BooleanField(default=False)    # display-over-other-apps (popup)
    notifications_granted = models.BooleanField(default=False)
    app_version = models.CharField(max_length=20, blank=True, default="")
    # Free-form popup/alarm health snapshot from the device: cached popup
    # config, SIM state, last popup shown/skipped + reason, alarm permissions.
    # Lets the admin see from Call Analytics WHY a device shows no popups.
    diagnostics = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App Device Status"
        verbose_name_plural = "App Device Statuses"

    def __str__(self):
        return f"{self.user.username} v{self.app_version} calls={self.calls_granted} overlay={self.overlay_granted}"


class PushDevice(models.Model):
    """An FCM device token belonging to a user, registered by the Android app.

    One user can have several devices. Tokens are upserted on app launch and
    pruned automatically when FCM reports them unregistered.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices"
    )
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=20, default="android")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.user.username} · {self.platform} · …{self.token[-12:]}"

"""Firm ops: audit log, firm settings, expenses."""

from datetime import date

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class ExpenseCategory(models.Model):
    """Office running-expense bucket (e.g. Electricity, Material, Marketing).

    Salaries are NOT a category — they are computed from Employee records.
    """

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Expense Category"
        verbose_name_plural = "Expense Categories"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name if self.is_active else f"{self.name} (archived)"


class Expense(models.Model):
    """An office running expense, either one-time or monthly-recurring.

    For RECURRING, `amount` is the per-month cost, `spent_on` is the first
    month it applies, and `end_on` (optional) is the last month (inclusive);
    a blank `end_on` means it is still ongoing.
    """

    TYPE_ONE_TIME = "one_time"
    TYPE_RECURRING = "recurring"
    TYPE_CHOICES = [
        (TYPE_ONE_TIME, "One-time"),
        (TYPE_RECURRING, "Recurring (monthly)"),
    ]

    category = models.ForeignKey(
        "ExpenseCategory", on_delete=models.PROTECT, related_name="expenses"
    )
    expense_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=TYPE_ONE_TIME)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)]
    )
    spent_on = models.DateField(
        default=timezone.localdate,
        help_text="One-time: date incurred. Recurring: first month it applies.",
    )
    end_on = models.DateField(
        null=True, blank=True,
        help_text="Recurring only: last month it applies (inclusive). Blank = ongoing.",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-spent_on", "-created_at"]
        indexes = [
            models.Index(fields=["expense_type", "spent_on"], name="expense_type_date_idx"),
        ]

    def __str__(self):
        return f"{self.category.name} · {self.get_expense_type_display()} · ₹{self.amount}"

    def applies_to_month(self, year, month):
        """True if this expense contributes cost to the given year/month."""
        if self.expense_type == self.TYPE_ONE_TIME:
            return self.spent_on.year == year and self.spent_on.month == month
        # Recurring: active if it started on/before this month and has not ended before it.
        from calendar import monthrange

        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        if self.spent_on > month_end:
            return False
        if self.end_on is not None and self.end_on < month_start:
            return False
        return True


class AuditLog(models.Model):
    """Append-only audit trail for sensitive events (sale approvals, role changes, etc.).

    Designed to be cheap to write from a signal handler: action is a free-form
    string, target is identified by model name + primary key (no FK so we don't
    cascade-delete history), and details holds arbitrary JSON.
    """
    ACTION_SALE_APPROVED = "sale.approved"
    ACTION_SALE_REJECTED = "sale.rejected"
    ACTION_SALE_PENDING = "sale.pending_again"
    ACTION_SALE_DELETED = "sale.deleted"
    ACTION_CLIENT_DELETED = "client.deleted"
    ACTION_EMPLOYEE_ROLE_CHANGED = "employee.role_changed"

    action = models.CharField(max_length=64, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_actions",
    )
    # Polymorphic target — model name + pk. No FK so a deleted target keeps history.
    target_model = models.CharField(max_length=64, blank=True, default="")
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    # Human-readable summary, ~one sentence.
    summary = models.CharField(max_length=255, blank=True, default="")
    # Structured payload (e.g. {"from": "pending", "to": "approved", "amount": "1500.00"}).
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target_model", "target_id"], name="audit_target_idx"),
            models.Index(fields=["action", "-created_at"], name="audit_action_time_idx"),
        ]

    def __str__(self):
        who = self.actor.username if self.actor_id else "system"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {who} · {self.action} · {self.summary}"


class FirmSettings(models.Model):
    """
    Singleton model to store firm/company details for branding in reports and documents.
    Only one instance should exist.
    """
    firm_name = models.CharField(max_length=200, default="")
    address = models.TextField(blank=True, help_text="Firm address")
    email = models.EmailField(blank=True, help_text="Contact email")
    phone = models.CharField(max_length=20, blank=True, help_text="Contact phone number")
    website = models.URLField(blank=True, help_text="Company website")
    logo = models.ImageField(
        upload_to="firm_logo/",
        blank=True,
        null=True,
        help_text="Firm logo (recommended: 200x60px PNG with transparent background)"
    )
    
    # Color theme for PDFs (optional)
    primary_color = models.CharField(
        max_length=7,
        default="#E5B740",
        help_text="Primary brand color (hex format, e.g., #E5B740)"
    )
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Firm Settings"
        verbose_name_plural = "Firm Settings"
    
    def __str__(self):
        return self.firm_name
    
    def save(self, *args, **kwargs):
        # Ensure only one instance exists (singleton pattern)
        if not self.pk and FirmSettings.objects.exists():
            raise ValueError("Only one FirmSettings instance is allowed. Please edit the existing settings.")
        super().save(*args, **kwargs)
    
    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance."""
        settings, created = cls.objects.get_or_create(pk=1)
        return settings

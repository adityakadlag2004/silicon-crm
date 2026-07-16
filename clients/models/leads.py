"""Lead Pipeline: leads, remarks, follow-ups, family, product progress."""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from .hr import Employee


# Calendar (calling component removed — see migration 0057+ for table drops)


class Lead(models.Model):
    STAGE_PENDING = "pending"
    STAGE_HALF = "half_sold"
    STAGE_PROCESSED = "processed"
    STAGE_CHOICES = [
        (STAGE_PENDING, "Pending"),
        (STAGE_HALF, "Half Sold"),
        (STAGE_PROCESSED, "Processed"),
    ]

    customer_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    data_received = models.BooleanField(default=False)
    data_received_on = models.DateField(null=True, blank=True)
    income = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    expenses = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    is_discarded = models.BooleanField(default=False, db_index=True)

    assigned_to = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="leads",
        help_text="Employee responsible for this lead",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_leads",
    )

    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_PENDING, db_index=True)
    converted_client = models.ForeignKey(
        'Client',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="source_lead",
        help_text="Client created from this lead via conversion",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer_name} ({self.assigned_to})"

    def compute_stage(self):
        statuses = list(self.progress_entries.values_list("status", flat=True))
        if not statuses:
            return self.STAGE_PENDING

        if all(s == LeadProductProgress.STATUS_PROCESSED for s in statuses):
            return self.STAGE_PROCESSED

        if any(s == LeadProductProgress.STATUS_PROCESSED for s in statuses):
            return self.STAGE_HALF

        if any(s == LeadProductProgress.STATUS_HALF for s in statuses):
            return self.STAGE_HALF

        return self.STAGE_PENDING

    def recompute_stage(self, save=True):
        new_stage = self.compute_stage()
        if save and new_stage != self.stage:
            self.stage = new_stage
            self.save(update_fields=["stage", "updated_at"])
        return new_stage


class LeadRemark(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="remarks")
    text = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Remark for {self.lead_id}"


class LeadFollowUp(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("done", "Done"),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="followups")
    assigned_to = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="lead_followups")
    scheduled_time = models.DateTimeField()
    note = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    # set once send_followup_reminders has notified the assignee
    reminded = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_time"]

    def __str__(self):
        return f"Follow-up for {self.lead_id} at {self.scheduled_time}"


class LeadFamilyMember(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="family_members")
    name = models.CharField(max_length=255)
    relation = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.relation})"


class LeadProductProgress(models.Model):
    PRODUCT_HEALTH = "health"
    PRODUCT_LIFE = "life"
    PRODUCT_WEALTH = "wealth"
    PRODUCT_CHOICES = [
        (PRODUCT_HEALTH, "Health"),
        (PRODUCT_LIFE, "Life"),
        (PRODUCT_WEALTH, "Wealth"),
    ]

    STATUS_PENDING = "pending"
    STATUS_HALF = "half_sold"
    STATUS_PROCESSED = "processed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_HALF, "Half Sold"),
        (STATUS_PROCESSED, "Processed"),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="progress_entries")
    product = models.CharField(max_length=20, choices=PRODUCT_CHOICES)
    target_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    achieved_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    remark = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("lead", "product")
        ordering = ["product"]

    def __str__(self):
        return f"{self.lead.customer_name} - {self.get_product_display()}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.lead.recompute_stage(save=True)

    def delete(self, *args, **kwargs):
        lead = self.lead
        super().delete(*args, **kwargs)
        lead.recompute_stage(save=True)

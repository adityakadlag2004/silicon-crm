"""Insurance policies, claims, and client meetings.

`Renewal` already records each premium *collection*; these models record the
*policy* it belongs to (number, insurer, sum insured, nominee) and what
happens when the client claims against it. One policy has many renewals.
"""

from django.conf import settings
from django.db import models


class InsurancePolicy(models.Model):
    """A policy held by a client — the thing renewals and claims hang off."""

    TYPE_HEALTH = "health"
    TYPE_LIFE = "life"
    TYPE_MOTOR = "motor"
    TYPE_OTHER = "other"
    TYPE_CHOICES = [
        (TYPE_HEALTH, "Health"), (TYPE_LIFE, "Life"),
        (TYPE_MOTOR, "Motor"), (TYPE_OTHER, "Other"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_LAPSED = "lapsed"
    STATUS_MATURED = "matured"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"), (STATUS_LAPSED, "Lapsed"),
        (STATUS_MATURED, "Matured"), (STATUS_CANCELLED, "Cancelled"),
    ]

    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="policies")
    policy_number = models.CharField(max_length=60, db_index=True)
    insurer = models.CharField(max_length=120, help_text="Insurance company, e.g. ICICI Lombard.")
    plan_name = models.CharField(max_length=160, blank=True)
    insurance_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_HEALTH)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE,
                              db_index=True)

    sum_insured = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    premium_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True, db_index=True)
    term_months = models.PositiveIntegerField(null=True, blank=True)

    nominee_name = models.CharField(max_length=120, blank=True)
    nominee_relationship = models.CharField(max_length=60, blank=True)
    relationship_manager = models.ForeignKey("Employee", null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name="policies")
    notes = models.TextField(blank=True)

    # The insurance sale this tracker entry was auto-created from, if any.
    # Keeps sale→policy sync idempotent (one policy per insurance sale) and
    # lets the tracker trace back to the booking. Manually-added policies and
    # ones synced from renewals leave this null.
    source_sale = models.OneToOneField("Sale", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="policy")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Insurance policies"
        ordering = ["-end_date", "policy_number"]

    def __str__(self):
        return f"{self.policy_number} · {self.client.name}"

    @property
    def is_expiring_soon(self):
        """True when the policy lapses within 30 days — drives the KPI tile."""
        from django.utils import timezone
        if not self.end_date or self.status != self.STATUS_ACTIVE:
            return False
        days = (self.end_date - timezone.localdate()).days
        return 0 <= days <= 30


class InsuranceClaim(models.Model):
    """A claim against a policy, from intimation through to settlement."""

    STATUS_INTIMATED = "intimated"
    STATUS_FILE_RECEIVED = "file_received"
    STATUS_SUBMITTED = "submitted"
    STATUS_SETTLED = "settled"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_INTIMATED, "Intimated"), (STATUS_FILE_RECEIVED, "File Received"),
        (STATUS_SUBMITTED, "Submitted"), (STATUS_SETTLED, "Settled"),
        (STATUS_REJECTED, "Rejected"),
    ]
    OPEN_STATUSES = (STATUS_INTIMATED, STATUS_FILE_RECEIVED, STATUS_SUBMITTED)

    MODE_CASHLESS = "cashless"
    MODE_REIMBURSEMENT = "reimbursement"
    MODE_CHOICES = [(MODE_CASHLESS, "Cashless"), (MODE_REIMBURSEMENT, "Reimbursement")]

    policy = models.ForeignKey(InsurancePolicy, on_delete=models.CASCADE, related_name="claims")
    claim_type = models.CharField(max_length=120, blank=True,
                                  help_text="e.g. Hospitalisation Claim.")
    claim_mode = models.CharField(max_length=15, choices=MODE_CHOICES, default=MODE_CASHLESS)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES,
                              default=STATUS_INTIMATED, db_index=True)

    intimation_date = models.DateField(null=True, blank=True)
    admission_date = models.DateField(null=True, blank=True)
    submission_date = models.DateField(null=True, blank=True)
    settlement_date = models.DateField(null=True, blank=True)

    claimed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    settled_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    settlement_details = models.TextField(blank=True)

    handled_by = models.ForeignKey("Employee", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="claims")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-intimation_date", "-id"]

    def __str__(self):
        return f"Claim on {self.policy.policy_number}"

    @property
    def shortfall(self):
        """Claimed minus settled — what the client did not get back."""
        return (self.claimed_amount or 0) - (self.settled_amount or 0)


class Meeting(models.Model):
    """A client meeting — held or scheduled. Drives the review cadence."""

    STATUS_SCHEDULED = "scheduled"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"), (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    KIND_REVIEW = "review"
    KIND_ONBOARDING = "onboarding"
    KIND_SERVICE = "service"
    KIND_SALES = "sales"
    KIND_CHOICES = [
        (KIND_REVIEW, "Portfolio Review"), (KIND_ONBOARDING, "Onboarding"),
        (KIND_SERVICE, "Service"), (KIND_SALES, "Sales"),
    ]

    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="meetings")
    employee = models.ForeignKey("Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="meetings")
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default=KIND_REVIEW)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_SCHEDULED, db_index=True)
    scheduled_at = models.DateTimeField(db_index=True)
    held_at = models.DateTimeField(null=True, blank=True)
    # Set when a review is booked forward — the "next meeting" report reads this.
    next_meeting_date = models.DateField(null=True, blank=True, db_index=True)
    outcome = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scheduled_at"]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.client.name}"

    @property
    def is_overdue(self):
        """Scheduled, but the slot has passed and nobody closed it off."""
        from django.utils import timezone
        return self.status == self.STATUS_SCHEDULED and self.scheduled_at < timezone.now()

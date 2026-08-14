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

    def save(self, *args, **kwargs):
        # The policy number is a matching key — the same normalisation Sale.save()
        # does, for the same reason: "ins123 " and "INS123" must not become two
        # policies. Guarded here rather than in each caller, because the number
        # arrives from the web form, the app API, insurance_sync and
        # link_renewal_to_policy, and only this point covers all four.
        self.policy_number = (self.policy_number or "").strip().upper()
        super().save(*args, **kwargs)

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


class ClaimActivity(models.Model):
    """Append-only trail for a claim: stage changes, notes, documents.

    Notes and the audit log share one model — a note is just an activity whose
    action is ``NOTE`` with the text in ``detail``. The claim workspace renders
    them as one timeline.
    """

    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    NOTE = "note"
    DOCUMENT_ADDED = "document_added"
    DOCUMENT_REMOVED = "document_removed"
    REMINDER_SET = "reminder_set"
    REMINDER_DONE = "reminder_done"
    ACTION_CHOICES = [
        (CREATED, "Claim raised"),
        (STATUS_CHANGED, "Stage changed"),
        (NOTE, "Note added"),
        (DOCUMENT_ADDED, "Document added"),
        (DOCUMENT_REMOVED, "Document removed"),
        (REMINDER_SET, "Reminder set"),
        (REMINDER_DONE, "Reminder completed"),
    ]

    claim = models.ForeignKey(InsuranceClaim, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    detail = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "Claim activities"

    def __str__(self):
        return f"{self.get_action_display()} on claim {self.claim_id}"


class ClaimDocument(models.Model):
    """A supporting document for a claim, stored in the client's Drive folder
    and served through a proxy view (same pattern as task attachments)."""

    KIND_CHOICES = [
        ("claim_form", "Claim Form"),
        ("bill", "Bill / Invoice"),
        ("discharge", "Discharge Summary"),
        ("prescription", "Prescription / Reports"),
        ("id_proof", "ID Proof"),
        ("settlement", "Settlement Letter"),
        ("other", "Other"),
    ]

    claim = models.ForeignKey(InsuranceClaim, on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="other")
    filename = models.CharField(max_length=255)
    mime = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    drive_file_id = models.CharField(max_length=128)
    drive_view_link = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.filename



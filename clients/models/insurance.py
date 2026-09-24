"""Insurance policies, claims, and client meetings.

`Renewal` already records each premium *collection*; these models record the
*policy* it belongs to (number, insurer, sum insured, nominee) and what
happens when the client claims against it. One policy has many renewals.
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property


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


class ExternalPolicy(models.Model):
    """A policy the client holds that the firm did NOT sell — an old LIC
    endowment, a pension plan, a health cover bought elsewhere.

    A separate table on purpose, not a flag on ``InsurancePolicy``: the tracker
    is the firm's own book, and its KPIs, renewal reminders, renewal linking,
    claims and the multiyear incentive gate all read it. An outside policy
    belongs in none of those. What it is for is the value-added service —
    knowing what the client already holds, and reminding them before a premium,
    a renewal, a money-back, the maturity or the pension falls due
    (``external_policy_reminders``). Every surface that shows one says
    "External Policy", so it can never be mistaken for business we booked.
    """

    # Reminder tasks point back here via Task.source_kind / source_id.
    TASK_SOURCE = "extpolicy"

    TYPE_HEALTH = "health"
    TYPE_TERM = "term"
    TYPE_ENDOWMENT = "endowment"
    TYPE_MONEY_BACK = "money_back"
    TYPE_WHOLE_LIFE = "whole_life"
    TYPE_CHILD = "child"
    TYPE_ULIP = "ulip"
    TYPE_PENSION = "pension"
    TYPE_MOTOR = "motor"
    TYPE_OTHER = "other"
    TYPE_CHOICES = [
        (TYPE_HEALTH, "Health Insurance"), (TYPE_TERM, "Term Life"),
        (TYPE_ENDOWMENT, "Endowment"), (TYPE_MONEY_BACK, "Money Back"),
        (TYPE_WHOLE_LIFE, "Whole Life"), (TYPE_CHILD, "Child Plan"),
        (TYPE_ULIP, "ULIP"), (TYPE_PENSION, "Pension / Annuity"),
        (TYPE_MOTOR, "Motor"), (TYPE_OTHER, "Other"),
    ]
    # Renewed term by term — the premium IS the renewal. Every other type runs
    # a fixed term to maturity with premiums due along the way.
    RENEWABLE = (TYPE_HEALTH, TYPE_MOTOR)

    STATUS_ACTIVE = "active"
    STATUS_PAID_UP = "paid_up"
    STATUS_LAPSED = "lapsed"
    STATUS_SURRENDERED = "surrendered"
    STATUS_MATURED = "matured"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "In force"), (STATUS_PAID_UP, "Paid-up"),
        (STATUS_LAPSED, "Lapsed"), (STATUS_SURRENDERED, "Surrendered"),
        (STATUS_MATURED, "Matured"),
    ]
    # Still produces dates. Paid-up stops the premiums, not the maturity.
    LIVE_STATUSES = (STATUS_ACTIVE, STATUS_PAID_UP)

    # Months between premiums, so the value is the interval itself.
    FREQUENCY_CHOICES = [(12, "Yearly"), (6, "Half-yearly"), (3, "Quarterly"),
                         (1, "Monthly"), (0, "Single premium")]

    client = models.ForeignKey("Client", on_delete=models.CASCADE,
                               related_name="external_policies")
    policy_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=TYPE_ENDOWMENT)
    insurer = models.CharField(max_length=120, help_text="e.g. LIC, HDFC Life, Star Health.")
    plan_name = models.CharField(max_length=160, blank=True)
    policy_number = models.CharField(max_length=60, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE,
                              db_index=True)

    sum_assured = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    premium_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    premium_frequency = models.PositiveSmallIntegerField(choices=FREQUENCY_CHOICES, default=12)
    start_date = models.DateField(help_text="Commencement date on the policy document.")
    premium_paying_term = models.PositiveSmallIntegerField(null=True, blank=True)
    policy_term = models.PositiveSmallIntegerField(null=True, blank=True)
    # Derived in save() — start date + policy term — so it can be queried.
    maturity_date = models.DateField(null=True, blank=True, db_index=True)
    maturity_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    bonus_accrued = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payout_every_years = models.PositiveSmallIntegerField(null=True, blank=True)
    payout_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pension_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pension_frequency = models.PositiveSmallIntegerField(choices=FREQUENCY_CHOICES[:4],
                                                         null=True, blank=True)

    nominee_name = models.CharField(max_length=120, blank=True)
    nominee_relationship = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "External policies"
        ordering = ["start_date", "id"]
        constraints = [
            # One record per real policy, or it is reminded twice.
            models.UniqueConstraint(
                fields=["client", "policy_number"], condition=~models.Q(policy_number=""),
                name="extpolicy_unique_number_per_client",
                violation_error_message="This client already has an external policy with this number.",
            ),
        ]

    def save(self, *args, **kwargs):
        from ..services.tasks import _add_months
        # Same normalisation as InsurancePolicy.save(): "lic123 " is "LIC123".
        self.policy_number = (self.policy_number or "").strip().upper()
        self.maturity_date = (
            _add_months(self.start_date, 12 * self.policy_term)
            if self.policy_term and self.start_date and not self.is_renewable else None)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.policy_number or 'External policy'} · {self.client.name}"

    @property
    def is_renewable(self):
        return self.policy_type in self.RENEWABLE

    @property
    def maturity_label(self):
        if self.policy_type == self.TYPE_TERM:
            return "Cover ends"
        if self.policy_type == self.TYPE_PENSION:
            return "Vesting · pension starts"
        return "Maturity"

    @property
    def maturity_value(self):
        """What reaches the client at the end. A term plan pays nothing when its
        cover ends, so its sum assured is never shown as money coming in."""
        if self.maturity_amount or self.policy_type == self.TYPE_TERM:
            return self.maturity_amount
        return self.sum_assured

    @cached_property
    def next_event(self):
        """The soonest thing due in the next five years, or None."""
        today = timezone.localdate()
        upcoming = self.events(today, today + timedelta(days=5 * 365))
        return upcoming[0] if upcoming else None

    def events(self, start, end):
        """Every dated event on this policy inside [start, end], soonest first.

        Nothing is stored: dates are read off the schedule (start date +
        frequency, PPT, term) so they can't drift from the policy. Each is a
        dict of date / kind / label / amount / policy. A policy that is not
        live produces nothing; a paid-up one stops paying premiums but still
        matures.
        """
        from ..services.tasks import _add_months

        def every(months, stop=None):
            # start_date + k·months for k ≥ 1 — the first premium was paid at
            # inception. Anchored on the start date, never stepped from the
            # previous one, so 31 Jan doesn't slide to the 28th for good.
            k = max(1, ((start.year - self.start_date.year) * 12
                        + start.month - self.start_date.month) // months)
            while True:
                d = _add_months(self.start_date, k * months)
                if d > end or (stop and d >= stop):
                    return
                if d >= start:
                    yield d
                k += 1

        def ev(d, kind, label, amount):
            return {"date": d, "kind": kind, "label": label, "amount": amount, "policy": self}

        if self.status not in self.LIVE_STATUSES or not self.start_date:
            return []
        if self.is_renewable:
            return [ev(d, "renewal", "Renewal due", self.premium_amount)
                    for d in every(12 * (self.policy_term or 1))]

        out = []
        if self.status == self.STATUS_ACTIVE and self.premium_frequency:
            premiums_end = (_add_months(self.start_date, 12 * self.premium_paying_term)
                            if self.premium_paying_term else self.maturity_date)
            out += [ev(d, "premium", "Premium due", self.premium_amount)
                    for d in every(self.premium_frequency, premiums_end)]
        if self.payout_every_years:
            out += [ev(d, "payout", "Money back", self.payout_amount)
                    for d in every(12 * self.payout_every_years, self.maturity_date)]
        if self.maturity_date and start <= self.maturity_date <= end:
            out.append(ev(self.maturity_date, "maturity", self.maturity_label,
                          self.maturity_value))
        return sorted(out, key=lambda e: e["date"])

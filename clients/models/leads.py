"""Lead Pipeline: SPANCO stages, product interests, remarks, follow-ups, family."""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .hr import Employee


# Calendar (calling component removed — see migration 0057+ for table drops)


class Lead(models.Model):
    """A lead worked through SPANCO, one stage at a time.

    The stage is a judgement the salesperson records — it is NOT derived from
    what has been sold. The old pipeline computed it from three hard-coded
    product rows (Health/Life/Wealth), which is why every lead was born
    needing all three whether or not it did. What a lead actually needs now
    lives in `interests`, picked per lead from the product catalog.
    """

    STAGE_SUSPECT = "suspect"
    STAGE_PROSPECT = "prospect"
    STAGE_APPROACH = "approach"
    STAGE_NEGOTIATION = "negotiation"
    STAGE_CONCLUSION = "conclusion"
    STAGE_ORDER = "order"
    STAGE_CHOICES = [
        (STAGE_SUSPECT, "Suspect"),
        (STAGE_PROSPECT, "Prospect"),
        (STAGE_APPROACH, "Approach / Analysis"),
        (STAGE_NEGOTIATION, "Negotiation"),
        (STAGE_CONCLUSION, "Conclusion"),
        (STAGE_ORDER, "Order"),
    ]
    # The pipeline runs in this order; a lead's position is its index here.
    STAGE_SEQUENCE = [s for s, _ in STAGE_CHOICES]

    # What each step means, shown on the stepper and in the app so the method
    # is taught by the screen rather than by a training deck.
    STAGE_HELP = {
        STAGE_SUSPECT: "Identified as a possible fit. Contact details only — nothing verified yet.",
        STAGE_PROSPECT: "Qualified: there is a real need, the budget exists and this person can decide.",
        STAGE_APPROACH: "Met or spoken to. Requirements analysed, our solution presented against them.",
        STAGE_NEGOTIATION: "Working through terms, premium, cover and conditions.",
        STAGE_CONCLUSION: "Agreed. Terms final, waiting on the signature / go-ahead.",
        STAGE_ORDER: "Paperwork in, business booked. Convert to a client and keep the relationship.",
    }

    # The middle of the pipeline: qualified, in play, and the stages where
    # attention actually changes the outcome. Suspect/Prospect are not worth
    # chasing yet and Order is already booked — these three are the day's work.
    STAGE_HOT = [STAGE_APPROACH, STAGE_NEGOTIATION, STAGE_CONCLUSION]

    STAGE_COLORS = {
        STAGE_SUSPECT: "#6B7280",
        STAGE_PROSPECT: "#B45309",
        STAGE_APPROACH: "#0369A1",
        STAGE_NEGOTIATION: "#7C3AED",
        STAGE_CONCLUSION: "#0F766E",
        STAGE_ORDER: "#15803D",
    }

    customer_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    data_received = models.BooleanField(default=False)
    data_received_on = models.DateField(null=True, blank=True)
    income = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    expenses = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    # "Lost / parked" in SPANCO terms. Kept under the old column name so no
    # historical row moves during the redesign.
    is_discarded = models.BooleanField(default=False, db_index=True)
    lost_reason = models.CharField(
        max_length=255, blank=True,
        help_text="Why the lead was dropped — this is what makes weak stages visible.",
    )

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

    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_SUSPECT, db_index=True)
    stage_changed_at = models.DateTimeField(default=timezone.now, db_index=True)
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

    @property
    def stage_index(self):
        """0-based position in SPANCO; -1 if the stage value is unknown."""
        try:
            return self.STAGE_SEQUENCE.index(self.stage)
        except ValueError:
            return -1

    @property
    def stage_color(self):
        return self.STAGE_COLORS.get(self.stage, "#6B5D3F")

    @property
    def stage_help(self):
        return self.STAGE_HELP.get(self.stage, "")

    @property
    def next_stage(self):
        """The stage after this one, or None at Order."""
        i = self.stage_index
        if i < 0 or i >= len(self.STAGE_SEQUENCE) - 1:
            return None
        return self.STAGE_SEQUENCE[i + 1]

    @property
    def days_in_stage(self):
        return (timezone.now() - self.stage_changed_at).days

    @property
    def is_won(self):
        return self.stage == self.STAGE_ORDER and not self.is_discarded


class LeadStageEvent(models.Model):
    """One recorded SPANCO move — what makes the funnel measurable.

    `to_stage` is a plain CharField, not a choice, because losing and
    reopening a lead are logged here too ("lost" / back to a real stage).
    """

    LOST = "lost"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="stage_events")
    from_stage = models.CharField(max_length=20, blank=True)
    to_stage = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.lead_id}: {self.from_stage or '—'} → {self.to_stage}"

    @staticmethod
    def label_for(stage):
        if stage == LeadStageEvent.LOST:
            return "Lost"
        return dict(Lead.STAGE_CHOICES).get(stage, stage or "—")

    @property
    def from_label(self):
        return self.label_for(self.from_stage)

    @property
    def to_label(self):
        return self.label_for(self.to_stage)


class LeadInterest(models.Model):
    """A product this lead actually needs, chosen per lead from the catalog.

    Replaces the fixed Health/Life/Wealth target grid: a lead that only wants
    a term plan carries one row, not three with two of them blank.
    `product` is nullable only so pre-SPANCO rows whose product no longer
    exists in the catalog survive the migration with their label in `note`.
    """

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="interests")
    product = models.ForeignKey(
        "Product", on_delete=models.PROTECT, null=True, blank=True, related_name="lead_interests",
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Indicative premium / SIP / cover being discussed. Optional.",
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("lead", "product")
        ordering = ["product__display_order", "id"]

    def __str__(self):
        return f"{self.lead.customer_name} – {self.label}"

    @property
    def label(self):
        return self.product.name if self.product_id else (self.note or "Other")


class LeadRemark(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="remarks")
    text = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Remark for {self.lead_id}"


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

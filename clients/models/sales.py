"""Sales, redemptions, net business/SIP entries."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Sale(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    POLICY_TYPE_FRESH = "fresh"
    POLICY_TYPE_PORT = "port"
    POLICY_TYPE_CHOICES = [
        (POLICY_TYPE_FRESH, "Fresh"),
        (POLICY_TYPE_PORT, "Port"),
    ]

    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="sales")
    # PROTECT: sales are the firm's business records — deleting an employee
    # must never silently erase their sales history (deactivate instead).
    employee = models.ForeignKey("Employee", on_delete=models.PROTECT, related_name="sales")
    product = models.CharField(max_length=50)
    product_ref = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")
    product_name_snapshot = models.CharField(max_length=100, blank=True, default="")

    # Business value (used for incentive calculation / points)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    # New: Cover amount (only relevant for Life & Health Insurance)
    cover_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    policy_type = models.CharField(
        max_length=10,
        choices=POLICY_TYPE_CHOICES,
        blank=True,
        default="",
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_sales",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    date = models.DateField(default=timezone.now, db_index=True)   # not auto_now_add

    # The sale is booked when the company APPROVES the policy, which is not the
    # day the policy actually starts. For Health/Life insurance the renewal
    # anniversary must track the policy's own commencement date (read off the
    # policy document), never the sale date — so it is captured separately and
    # made mandatory for those products in the sale forms.
    policy_date = models.DateField(
        null=True, blank=True, db_index=True,
        help_text="Policy commencement date from the policy document "
                  "(drives the annual renewal reminder). Insurance only.",
    )
    # The insurer's policy number, read off the document. Mandatory for
    # Health/Life in the sale forms — it's what links this sale to its policy
    # on the Insurance Tracker and to future renewals of the same policy.
    policy_number = models.CharField(max_length=60, blank=True, db_index=True)

    points = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    incentive_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    # Records which campaign (if any) awarded the points on this sale; null = regular mechanism.
    campaign = models.ForeignKey("Campaign", null=True, blank=True, on_delete=models.SET_NULL, related_name="sales")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["employee", "date"], name="sale_emp_date_idx"),
            models.Index(fields=["employee", "product", "date"], name="sale_emp_prod_date_idx"),
        ]

    def _is_health_product(self):
        if self.product_ref_id:
            return self.product_ref.code == "HEALTH_INS" or (self.product_ref.name or "").strip().lower() == "health insurance"
        return (self.product or "").strip().lower() == "health insurance"

    @property
    def is_insurance(self):
        """Health or Life insurance — the products that carry a policy date
        and an annual renewal anniversary distinct from the sale date."""
        codes = {"HEALTH_INS", "LIFE_INS"}
        names = {"health insurance", "life insurance"}
        if self.product_ref_id:
            return (self.product_ref.code in codes
                    or (self.product_ref.name or "").strip().lower() in names)
        return (self.product or "").strip().lower() in names

    @property
    def renewal_basis(self):
        """The date the annual renewal anniversary is measured from.

        Policy date when known; the sale date is only a fallback for legacy
        rows entered before policy_date existed.
        """
        return self.policy_date or self.date

    def policy_anniversary(self, on_or_after=None):
        """Next yearly renewal date on/after `on_or_after` (default today).

        Returns None for non-insurance sales or when there is no basis date.
        A 29 Feb policy renews on 28 Feb in common years (insurers treat the
        policy as continuous, unlike a birthday we'd rather skip)."""
        if not self.is_insurance:
            return None
        basis = self.renewal_basis
        if not basis:
            return None
        from datetime import date as _date
        ref = on_or_after or timezone.localdate()
        year = ref.year
        for candidate_year in (year, year + 1):
            try:
                anniversary = basis.replace(year=candidate_year)
            except ValueError:                      # 29 Feb → 28 Feb
                anniversary = basis.replace(year=candidate_year, day=28)
            if anniversary >= ref:
                return anniversary
        return None

    def _effective_product_label(self):
        if self.product_ref_id:
            return self.product_ref.name
        return self.product

    def _active_campaign_product(self):
        """Return the single CampaignProduct covering this sale's product on its
        date, or None. Overlap prevention guarantees at most one match."""
        from .incentives import CampaignProduct

        sale_date = self.date or timezone.localdate()
        qs = CampaignProduct.objects.select_related("campaign").filter(
            campaign__is_active=True,
            campaign__start_date__lte=sale_date,
            campaign__end_date__gte=sale_date,
        )
        if self.product_ref_id:
            qs = qs.filter(product_ref_id=self.product_ref_id)
        else:
            qs = qs.filter(product_ref__name=self._effective_product_label())
        return qs.first()

    def _compute_campaign_points(self, cp):
        """Compute points from a CampaignProduct benefit (replaces regular)."""
        from .incentives import CampaignSlab

        if cp.benefit_type == cp.BENEFIT_UNIT:
            unit = cp.unit_amount or Decimal("0")
            if unit > 0:
                self.points = (self.amount / unit) * (cp.points_per_unit or Decimal("0"))
                self.incentive_amount = self.points
            else:
                self.points = Decimal("0.000")
                self.incentive_amount = Decimal("0.00")
            return

        # One-time target payout: slab-delta over the campaign window.
        slab_qs = CampaignSlab.objects.filter(campaign_product=cp).order_by("-threshold")
        if not slab_qs.exists():
            self.points = Decimal("0.000")
            self.incentive_amount = Decimal("0.00")
            return

        campaign = cp.campaign
        premium = self.amount or Decimal("0")
        # Only approved sales count toward slab thresholds — a rejected sale
        # must not push a colleague row over a payout boundary.
        qs = Sale.objects.filter(
            employee=self.employee,
            status=Sale.STATUS_APPROVED,
            date__range=[campaign.start_date, campaign.end_date],
        )
        if self.product_ref_id:
            qs = qs.filter(product_ref_id=self.product_ref_id)
        else:
            qs = qs.filter(product=self._effective_product_label())
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        cumulative_amount = (qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")) + premium

        payout = Decimal("0.00")
        for slab in slab_qs:
            if cumulative_amount >= slab.threshold:
                payout = slab.payout
                break

        already_awarded = qs.aggregate(total=Sum("points"))["total"] or Decimal("0.00")
        delta = payout - already_awarded
        if delta < 0:
            delta = Decimal("0.00")
        self.points = delta
        self.incentive_amount = delta

    def compute_points(self):
        """Compute points based on IncentiveRule + IncentiveSlab in DB"""
        from .incentives import IncentiveRule, IncentiveSlab

        product_label = self._effective_product_label()

        # Rejected sales earn nothing.
        if self.status == self.STATUS_REJECTED:
            self.campaign = None
            self.points = Decimal("0.000")
            self.incentive_amount = Decimal("0.00")
            return

        if self._is_health_product() and self.policy_type == self.POLICY_TYPE_PORT:
            self.campaign = None
            self.points = Decimal("0.000")
            self.incentive_amount = Decimal("0.00")
            return

        # Time-bound campaign takes precedence and fully replaces regular points.
        cp = self._active_campaign_product()
        if cp is not None:
            self.campaign = cp.campaign
            self._compute_campaign_points(cp)
            return
        self.campaign = None

        try:
            rule_qs = IncentiveRule.objects.filter(active=True)
            if self.product_ref_id:
                rule_qs = rule_qs.filter(product_ref=self.product_ref)
            else:
                rule_qs = rule_qs.filter(product=product_label)
            # first() instead of get(): a stray duplicate rule must degrade to
            # deterministic behaviour, not crash every save of this product.
            rule = rule_qs.order_by("id").first()
            if rule is None:
                raise IncentiveRule.DoesNotExist

            # Check if this rule has slabs → slab-based calculation
            slab_qs = IncentiveSlab.objects.filter(rule=rule).order_by("-threshold")

            if slab_qs.exists():
                # Slab-based incentive (e.g. Life Insurance)
                premium = self.amount or Decimal("0")

                if not rule.active:
                    self.points = Decimal("0.000")
                    self.incentive_amount = Decimal("0.00")
                    return

                sale_month = self.date.month if self.date else timezone.now().month
                sale_year = self.date.year if self.date else timezone.now().year
                # Only approved sales count toward the monthly slab cumulative.
                qs = Sale.objects.filter(
                    employee=self.employee,
                    status=Sale.STATUS_APPROVED,
                    date__year=sale_year,
                    date__month=sale_month,
                )
                if self.product_ref_id:
                    qs = qs.filter(product_ref=self.product_ref)
                else:
                    qs = qs.filter(product=product_label)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
                cumulative_amount = (qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")) + premium

                # Match highest slab threshold <= cumulative amount
                payout = Decimal("0.00")
                for slab in slab_qs:
                    if cumulative_amount >= slab.threshold:
                        payout = slab.payout
                        break

                already_awarded = qs.aggregate(total=Sum("points"))["total"] or Decimal("0.00")
                delta = payout - already_awarded
                if delta < 0:
                    delta = Decimal("0.00")

                self.points = delta
                self.incentive_amount = delta
                return

            # Unit-based incentive (e.g. SIP, PMS, etc.)
            if rule.unit_amount > 0:
                self.points = (self.amount / rule.unit_amount) * rule.points_per_unit
                self.incentive_amount = self.points  # You can later define ₹ conversion
            else:
                self.points = Decimal("0.000")
                self.incentive_amount = Decimal("0.00")
        except IncentiveRule.DoesNotExist:
            self.points = Decimal("0.000")
            self.incentive_amount = Decimal("0.00")

    def save(self, *args, **kwargs):
        if self.product_ref_id:
            self.product = self.product_ref.name
            self.product_name_snapshot = self.product_ref.name
        elif self.product:
            self.product_name_snapshot = self.product

        if not self._is_health_product():
            self.policy_type = ""
        self.compute_points()  # always compute before saving
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.client} - {self.product} - ₹{self.amount}"


class Redemption(models.Model):
    """Manual adjustment entries not linked to any customer.

    Used for lumsum redemptions and SIP stoppage records. Managers can add
    these to adjust net business calculations.
    """
    TYPE_CHOICES = [
        ("redemption", "Redemption (Lumsum)"),
        ("sip_stoppage", "SIP Stoppage"),
    ]

    product = models.CharField(max_length=50)
    product_ref = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="redemptions")
    entry_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="redemption")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.get_entry_type_display()} - {self.product} : ₹{self.amount} on {self.date}"


class NetBusinessEntry(models.Model):
    ENTRY_CHOICES = [
        ("sale", "Sale"),
        ("redemption", "Redemption"),
    ]

    entry_type = models.CharField(max_length=20, choices=ENTRY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.entry_type.title()} ₹{self.amount} on {self.date}"


class NetSipEntry(models.Model):
    ENTRY_CHOICES = [
        ("fresh", "SIP Fresh"),
        ("stopped", "SIP Stopped"),
    ]

    entry_type = models.CharField(max_length=20, choices=ENTRY_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.get_entry_type_display()} ₹{self.amount} on {self.date}"

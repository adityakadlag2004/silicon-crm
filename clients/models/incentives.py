"""Incentive rules/slabs, campaigns, monthly incentive snapshots."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


# ---------- IncentiveRule (configurable in admin) ----------
class IncentiveRule(models.Model):
    MODE_BONUS = "bonus"
    MODE_RATE = "rate"
    MODE_CHOICES = [
        (MODE_BONUS, "Slab payout is a rupee bonus (earned-to-date ladder)"),
        (MODE_RATE, "Slab payout is a % rate applied to the sale"),
    ]

    PERIOD_MONTH = "month"
    PERIOD_FY = "fy"
    PERIOD_CHOICES = [
        (PERIOD_MONTH, "Calendar month"),
        (PERIOD_FY, "Financial year (Apr–Mar)"),
    ]

    product = models.CharField(max_length=50, unique=True)
    product_ref = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="incentive_rules")
    unit_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Base unit (e.g., 1000 or 100000)",
    )
    points_per_unit = models.DecimalField(max_digits=12, decimal_places=3,
                                          help_text="Points awarded per unit_amount")
    # A rule's slabs are read one of two ways. "bonus" (the historical shape)
    # treats the payout as rupees earned-to-date and pays only the delta, on top
    # of the flat unit rate. "rate" treats the payout as a percent, resolved
    # from the period's volume and applied to this sale instead of the unit rate.
    slab_mode = models.CharField(
        max_length=10, choices=MODE_CHOICES, default=MODE_BONUS,
        help_text="How this rule's slab payouts are read.",
    )
    slab_period = models.CharField(
        max_length=10, choices=PERIOD_CHOICES, default=PERIOD_MONTH,
        help_text="Window the cumulative volume driving the slabs is measured over.",
    )
    port_percent = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Health insurance only: flat % paid on Port policies, which sit "
                  "outside the Fresh volume ladder. Blank = Port earns nothing.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Incentive Rule"
        verbose_name_plural = "Incentive Rules"

    def __str__(self):
        return f"{self.product}: {self.points_per_unit} pts per {self.unit_amount}"


class IncentiveSlab(models.Model):
    """One rung of a rule's ladder, matched against the employee's cumulative
    volume over the rule's ``slab_period``. What ``payout`` means depends on the
    rule's ``slab_mode`` — rupees earned-to-date, or a % rate for the band."""
    rule = models.ForeignKey(IncentiveRule, on_delete=models.CASCADE, related_name="slabs")
    threshold = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Cumulative amount threshold, over the rule's slab period"
    )
    payout = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Rupees earned-to-date at this threshold (bonus mode), or the "
                  "% rate paid on each sale in this band (rate mode)"
    )
    label = models.CharField(max_length=100, blank=True,
                             help_text="Optional label, e.g. 'Gold Slab'")

    class Meta:
        verbose_name = "Incentive Slab"
        verbose_name_plural = "Incentive Slabs"
        ordering = ["-threshold"]  # highest first for slab matching
        unique_together = [("rule", "threshold")]

    def __str__(self):
        return f"{self.rule.product} – ₹{self.threshold} → {self.payout} pts"


class IncentiveAccrual(models.Model):
    """Points issued for a later year of a multiyear health policy.

    The client pays a 2- or 3-year premium up front, but only the first year is
    business now — so only that year's points are issued at sale time. Each
    later year's points fall due on the policy's anniversary in that year, with
    no renewal to collect and nothing for anyone to enter: the premium is
    already paid, so all that happens is the points arrive.

    One row per (sale, year), created by the ``multiyear_incentive_accruals``
    cron. The unique constraint is what makes a re-run harmless.
    """

    sale = models.ForeignKey("Sale", on_delete=models.CASCADE, related_name="accruals")
    employee = models.ForeignKey("Employee", on_delete=models.PROTECT, related_name="incentive_accruals")
    year_index = models.PositiveSmallIntegerField(
        help_text="Which policy year these points are for (2 = second year).",
    )
    due_date = models.DateField(db_index=True, help_text="The policy anniversary these points fell due on.")
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="The year's premium slice these points were computed on.",
    )
    points = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Incentive Accrual"
        verbose_name_plural = "Incentive Accruals"
        ordering = ["-due_date", "-id"]
        unique_together = [("sale", "year_index")]
        indexes = [models.Index(fields=["employee", "due_date"], name="accrual_emp_due_idx")]

    def __str__(self):
        return f"{self.sale_id} year {self.year_index}: {self.points} pts on {self.due_date}"


# ---------- Target & Special Campaigns (time-bound, product-wise) ----------
class Campaign(models.Model):
    """A time-bound promotion. Sales of the campaign's products, dated within
    [start_date, end_date], earn the campaign benefit instead of the regular
    IncentiveRule. Outside the window (or when inactive) sales fall back to the
    regular mechanism automatically — the check is against the sale's date."""

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(
        default=True,
        help_text="Manual kill switch. A campaign only applies when active AND the sale date is within the window.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-end_date", "-created_at"]

    def __str__(self):
        return f"{self.name} ({self.start_date}–{self.end_date})"


class CampaignProduct(models.Model):
    """Per-product benefit configuration inside a campaign."""

    BENEFIT_UNIT = "unit"
    BENEFIT_TARGET = "target"
    BENEFIT_CHOICES = [
        (BENEFIT_UNIT, "Custom points per unit"),
        (BENEFIT_TARGET, "One-time target payout"),
    ]

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="products")
    product_ref = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="campaign_products")
    benefit_type = models.CharField(max_length=10, choices=BENEFIT_CHOICES, default=BENEFIT_UNIT)
    unit_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Base unit (e.g., 1000). Used for the 'Custom points per unit' benefit.",
    )
    points_per_unit = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        help_text="Points awarded per unit_amount. Used for the 'Custom points per unit' benefit.",
    )

    class Meta:
        unique_together = [("campaign", "product_ref")]

    def __str__(self):
        return f"{self.campaign.name} – {self.product_ref.name} ({self.get_benefit_type_display()})"


class CampaignSlab(models.Model):
    """Campaign-scoped slab for the 'One-time target payout' benefit. When the
    employee's cumulative amount for this product WITHIN the campaign window
    reaches `threshold`, the `payout` is awarded (delta-protected)."""

    campaign_product = models.ForeignKey(CampaignProduct, on_delete=models.CASCADE, related_name="slabs")
    threshold = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Cumulative amount threshold within the campaign window.",
    )
    payout = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Points/payout awarded when the threshold is reached.",
    )
    label = models.CharField(max_length=100, blank=True, help_text="Optional label, e.g. 'Gold Slab'")

    class Meta:
        ordering = ["-threshold"]  # highest first for slab matching
        unique_together = [("campaign_product", "threshold")]

    def __str__(self):
        return f"{self.campaign_product.product_ref.name} – ₹{self.threshold} → {self.payout} pts"


def campaign_product_overlaps(product_ref, start_date, end_date, exclude_campaign_id=None):
    """True if `product_ref` is already covered by another campaign whose date
    window intersects [start_date, end_date]. Two ranges overlap when each
    starts on or before the other ends."""
    if not product_ref or not start_date or not end_date:
        return False
    qs = CampaignProduct.objects.filter(
        product_ref=product_ref,
        campaign__start_date__lte=end_date,
        campaign__end_date__gte=start_date,
    )
    if exclude_campaign_id is not None:
        qs = qs.exclude(campaign_id=exclude_campaign_id)
    return qs.exists()

"""Product catalog and revenue-band margin slabs."""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class ProductQuerySet(models.QuerySet):
    """Where the main-product rule lives.

    Sub-products exist so a sale (or a renewal) can name the exact plan sold.
    They are an entry-time detail and nothing else: every other picker, filter
    and report in the system deals in top-level products, with a sub-product's
    business folded into its category. Only the sale and renewal entry forms
    (web + app) may offer `.selectable()` without `.main()`.
    """

    def main(self):
        """Top-level products only — categories and standalone products."""
        return self.filter(parent__isnull=True)

    def selectable(self):
        """Active and unarchived: what a picker is allowed to offer."""
        return self.filter(is_active=True, archived_at__isnull=True)

    def in_display_order(self):
        return self.order_by("display_order", "name")


class Product(models.Model):
    DOMAIN_SALE = "sale"
    DOMAIN_RENEWAL = "renewal"
    DOMAIN_BOTH = "both"
    DOMAIN_CHOICES = [
        (DOMAIN_SALE, "Sales"),
        (DOMAIN_RENEWAL, "Renewals"),
        (DOMAIN_BOTH, "Both"),
    ]

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="children",
        help_text="Optional category. Set this to make the product a sub-product "
                  "(e.g. 'Term Plan' under 'Life Insurance') — it inherits the "
                  "category's insurance behaviour and carries its own margin.",
    )
    domain = models.CharField(max_length=20, choices=DOMAIN_CHOICES, default=DOMAIN_BOTH)
    display_order = models.IntegerField(default=0)
    margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text="Default business margin % on this product's sale revenue, used when no margin slab matches.",
    )
    renewal_margin_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text="Business margin % applied to this product's renewal premium (only used for renewal-tracked products).",
    )
    is_active = models.BooleanField(default=True)
    show_in_reports = models.BooleanField(
        default=True,
        help_text="Show this product as a column on the daily/monthly business report. "
                  "Turn it off for a product nobody reads on that sheet — the sales "
                  "themselves are unaffected.",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        state = "Archived" if self.archived_at else "Active"
        return f"{self.name} ({state})"

    @property
    def category_id(self):
        """The id business rolls up to — the parent's, or its own."""
        return self.parent_id or self.pk

    def archive(self, reason=""):
        self.is_active = False
        self.archived_at = timezone.now()
        self.archived_reason = (reason or "").strip()
        self.save(update_fields=["is_active", "archived_at", "archived_reason", "updated_at"])

    @property
    def category(self):
        """The top-level product this rolls up to (itself if it has no parent).

        Insurance behaviour (Health vs Life, Fresh/Port) is a property of the
        category, so a sub-product like 'Term Plan' answers as its 'Life
        Insurance' parent would.
        """
        return self.parent if self.parent_id else self

    @property
    def is_health(self):
        """Health-insurance products carry Fresh/Port margin distinctions."""
        c = self.category
        return c.code == "HEALTH_INS" or (c.name or "").strip().lower() == "health insurance"

    @property
    def is_insurance(self):
        """Health or Life — products (and their sub-products) that need a policy date."""
        c = self.category
        return (c.code in {"HEALTH_INS", "LIFE_INS"}
                or (c.name or "").strip().lower() in {"health insurance", "life insurance"})

    @property
    def tracks_renewals(self):
        return self.domain in (self.DOMAIN_RENEWAL, self.DOMAIN_BOTH)

    def margin_for(self, amount, policy_type=""):
        """Resolve the margin % for a given cumulative revenue amount.

        For health products, `policy_type` selects the Fresh/Port slab set.
        Falls back to the product's default `margin_percent` if no slab matches.
        """
        amount = Decimal(str(amount or 0))
        pt = (policy_type or "").strip() if self.is_health else ""
        # Filtered and sorted in Python so a prefetch_related("margin_slabs")
        # is actually used — .filter()/.order_by() here would ignore it and go
        # back to the database once per call, which is how the margin report
        # ended up querying slabs per product per bucket.
        slabs = sorted(
            (s for s in self.margin_slabs.all() if s.policy_type == pt),
            key=lambda s: s.min_amount,
        )
        for slab in slabs:
            if amount < slab.min_amount:
                continue
            if slab.max_amount is not None and amount > slab.max_amount:
                continue
            return slab.margin_percent
        return self.margin_percent

    @property
    def has_ppt_rates(self):
        """True for plans whose margin is driven by Premium Paying Term."""
        return self.ppt_rates.exists()

    def ppt_choices(self, mdrt=False):
        """[(ppt, fyc)] for the active designation, in the chart's PPT order."""
        desig = PlanPptRate.DESIG_MDRT if mdrt else PlanPptRate.DESIG_ADVISOR
        rows = self.ppt_rates.filter(designation=desig).exclude(fyc__isnull=True)
        return [(r.ppt, r.fyc) for r in sorted(rows, key=_ppt_sort_key)]

    def fyc_for_ppt(self, ppt, mdrt=False):
        """The FYC (sale margin %) for a PPT at the active designation, or None."""
        desig = PlanPptRate.DESIG_MDRT if mdrt else PlanPptRate.DESIG_ADVISOR
        row = self.ppt_rates.filter(designation=desig, ppt=str(ppt)).first()
        return row.fyc if row else None


def _ppt_sort_key(rate):
    """SP first, then numeric PPT ('12+' sorts as 12)."""
    p = (rate.ppt if hasattr(rate, "ppt") else rate).strip()
    if p.upper() == "SP":
        return (0, 0)
    return (1, int(p.rstrip("+") or 0))


class PlanPptRate(models.Model):
    """Commission rate for a life-insurance plan at a given Premium Paying Term.

    Sourced from the insurer's Agency FYC-RYC chart (docs/insurance/). FYC (first
    year commission %) is the sale margin and rises with PPT. Rates are stored per
    designation — Advisor by default, MDRT when the agency has qualified for the
    financial year (see FirmSettings.is_mdrt_active). Renewal (RYC) rates are the
    without-rider figures; the with-rider distinction only moves MDRT rider rows
    and is intentionally not modelled.
    """

    DESIG_ADVISOR = "advisor"
    DESIG_MDRT = "mdrt"
    DESIG_CHOICES = [(DESIG_ADVISOR, "Advisor"), (DESIG_MDRT, "MDRT")]

    product = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="ppt_rates")
    designation = models.CharField(max_length=10, choices=DESIG_CHOICES, default=DESIG_ADVISOR)
    ppt = models.CharField(
        max_length=4,
        help_text='Premium Paying Term: "SP" (single premium), or years like "5", "12+".',
    )
    fyc = models.DecimalField(
        "First-year commission %", max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    ryc_2nd = models.DecimalField("Renewal % (2nd yr)", max_digits=6, decimal_places=2, null=True, blank=True)
    ryc_3rd = models.DecimalField("Renewal % (3rd yr)", max_digits=6, decimal_places=2, null=True, blank=True)
    ryc_4th = models.DecimalField("Renewal % (4th yr)", max_digits=6, decimal_places=2, null=True, blank=True)
    ryc_5plus = models.DecimalField("Renewal % (5+ yr)", max_digits=6, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "Plan PPT Rate"
        verbose_name_plural = "Plan PPT Rates"
        unique_together = [("product", "designation", "ppt")]
        ordering = ["product", "designation", "ppt"]

    def __str__(self):
        return f"{self.product.code} {self.get_designation_display()} PPT {self.ppt}: {self.fyc}%"


class ProductMarginSlab(models.Model):
    """Revenue-band margin override for a product.

    The slab is matched against the cumulative monthly revenue of the product
    (per policy type, for health insurance). The top slab can leave
    `max_amount` blank to mean "and above".
    """

    POLICY_TYPE_CHOICES = [
        ("", "All / Not applicable"),
        ("fresh", "Fresh"),
        ("port", "Port"),
    ]

    product = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="margin_slabs")
    policy_type = models.CharField(
        max_length=10,
        choices=POLICY_TYPE_CHOICES,
        blank=True,
        default="",
        help_text="Fresh / Port for Health Insurance; leave blank for other products.",
    )
    min_amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)],
        help_text="Lower bound of cumulative monthly revenue (inclusive).",
    )
    max_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text="Upper bound (inclusive). Leave blank for the top 'and above' slab.",
    )
    margin_percent = models.DecimalField(
        max_digits=6, decimal_places=2, validators=[MinValueValidator(0)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Product Margin Slab"
        verbose_name_plural = "Product Margin Slabs"
        ordering = ["product", "policy_type", "min_amount"]

    def __str__(self):
        ceiling = f"{self.max_amount}" if self.max_amount is not None else "∞"
        pt = f" [{self.get_policy_type_display()}]" if self.policy_type else ""
        return f"{self.product.name}{pt}: ₹{self.min_amount}–{ceiling} → {self.margin_percent}%"

"""Product catalog and revenue-band margin slabs."""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Product(models.Model):
    DOMAIN_SALE = "sale"
    DOMAIN_RENEWAL = "renewal"
    DOMAIN_BOTH = "both"
    DOMAIN_CHOICES = [
        (DOMAIN_SALE, "Sales"),
        (DOMAIN_RENEWAL, "Renewals"),
        (DOMAIN_BOTH, "Both"),
    ]

    # RTA feed cross-check: products flagged here get their pending sales
    # verified against the client's PAN in the imported CAMS/KFintech data.
    RTA_MATCH_NONE = ""
    RTA_MATCH_SIP = "sip"
    RTA_MATCH_LUMPSUM = "lumpsum"
    RTA_MATCH_ANY = "any"
    RTA_MATCH_CHOICES = [
        (RTA_MATCH_NONE, "Not RTA-linked"),
        (RTA_MATCH_SIP, "MF SIP installments"),
        (RTA_MATCH_LUMPSUM, "MF lumpsum purchases"),
        (RTA_MATCH_ANY, "Any MF transaction"),
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
    rta_match = models.CharField(
        max_length=10, blank=True, default=RTA_MATCH_NONE, choices=RTA_MATCH_CHOICES,
        help_text="If set, pending sales of this product are cross-checked against RTA feed transactions by client PAN.",
    )
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
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        state = "Archived" if self.archived_at else "Active"
        return f"{self.name} ({state})"

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
        for slab in self.margin_slabs.filter(policy_type=pt):
            if amount < slab.min_amount:
                continue
            if slab.max_amount is not None and amount > slab.max_amount:
                continue
            return slab.margin_percent
        return self.margin_percent


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

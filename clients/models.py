import logging
import re
import uuid

from datetime import date, time as datetime_time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import connection, models, transaction
from django.db.models import Max, Sum
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

# Compiled once at module load — used by MessageTemplate.render() for safe variable substitution.
_TEMPLATE_VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')


class Employee(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=50, choices=(("admin", "Admin"), ("manager", "Manager"), ("employee", "Employee")))
    active = models.BooleanField(default=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employee_number = models.CharField(max_length=50, unique=True, null=True, blank=True)

    def __str__(self):
        return self.user.username


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
    def is_health(self):
        """Health-insurance products carry Fresh/Port margin distinctions."""
        return self.code == "HEALTH_INS" or (self.name or "").strip().lower() == "health insurance"

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


class Client(models.Model):
    # Override default id with our own serial number
    id = models.IntegerField(primary_key=True, unique=True, editable=False)

    name = models.CharField(max_length=200, db_index=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True, db_index=True)
    pan = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    # Optional date of birth to support Birthday Calls in calendar
    date_of_birth = models.DateField(null=True, blank=True)

    mapped_to = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)

    # SIP details
    sip_status = models.BooleanField(default=False)
    sip_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    sip_topup = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Lumsum investment (separate from SIP)
    lumsum_investment = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, default=0)

    # Health Insurance details
    health_status = models.BooleanField(default=False)
    health_cover = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    health_topup = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    health_product = models.CharField(max_length=200, blank=True, null=True)

    # Life Insurance details
    life_status = models.BooleanField(default=False)
    life_cover = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    life_product = models.CharField(max_length=200, blank=True, null=True)

    # Motor Insurance details
    motor_status = models.BooleanField(default=False)
    motor_insured_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    motor_product = models.CharField(max_length=200, blank=True, null=True)

    # PMS details
    pms_status = models.BooleanField(default=False)
    pms_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    pms_start_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, default="Unmapped")
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    edited_by = models.ForeignKey('Employee', null=True, blank=True, on_delete=models.SET_NULL, related_name='edited_clients')

    # Google Drive doc-folder (created lazily on first request from the client profile page).
    drive_folder_id = models.CharField(max_length=100, blank=True, default="")
    drive_folder_url = models.URLField(max_length=500, blank=True, default="")

    def __str__(self):
        return f"{self.id} - {self.name}"

    # Arbitrary but stable key for the advisory lock serializing id allocation.
    _ID_ALLOC_LOCK_KEY = 815001

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        # Normalize lumsum investment to 0 if missing
        if self.lumsum_investment is None:
            self.lumsum_investment = Decimal("0.00")

        if self.id is None:
            # Auto-generate sequential id. Max()+1 alone races under concurrency,
            # and a duplicate id would make Django silently UPDATE the other
            # client's row — so serialize allocation with a Postgres advisory
            # lock (released on commit) and force an INSERT so a collision can
            # only ever fail loudly, never overwrite.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [self._ID_ALLOC_LOCK_KEY])
                max_id = Client.objects.aggregate(max_id=Max('id'))['max_id'] or 0
                self.id = max_id + 1
                kwargs["force_insert"] = True
                super().save(*args, **kwargs)
            return

        # Ensure edited_at is set at least once after the first edit so
        # the "Show Edited" filter can surface historical edits.
        if self.edited_at is None and not is_new:
            self.edited_at = timezone.now()
        super().save(*args, **kwargs)
    
    def reassign_to(self, new_employee, changed_by=None, note=''):
        """
        Atomically reassign this client to `new_employee` and create an audit entry.

        Returns:
            (changed: bool, previous_employee, new_employee)
        """
        previous = self.mapped_to
        if previous == new_employee:
            return False, previous, new_employee

        with transaction.atomic():
            self.mapped_to = new_employee
            self.status = "Mapped" if new_employee else "Unmapped"
            self.save(update_fields=['mapped_to', 'status'])

            # create audit entry
            ClientMappingAudit.objects.create(
                client=self,
                previous_employee=previous,
                new_employee=new_employee,
                changed_by=changed_by,
                changed_at=timezone.now(),
                note=note or ''
            )

        return True, previous, new_employee



class ClientMappingAudit(models.Model):
    client = models.ForeignKey('Client', on_delete=models.CASCADE, related_name='mapping_audits')
    previous_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    new_employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    changed_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f"Client {self.client_id}: {self.previous_employee} → {self.new_employee} at {self.changed_at}"


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


class Renewal(models.Model):
    PRODUCT_TYPE_LIFE = "life_insurance"
    PRODUCT_TYPE_HEALTH = "health_insurance"
    PRODUCT_TYPE_OTHER = "other"
    PRODUCT_TYPE_CHOICES = [
        (PRODUCT_TYPE_LIFE, "Life Insurance"),
        (PRODUCT_TYPE_HEALTH, "Health Insurance"),
        (PRODUCT_TYPE_OTHER, "Other"),
    ]

    FREQUENCY_MONTHLY = "monthly"
    FREQUENCY_QUARTERLY = "quarterly"
    FREQUENCY_HALF_YEARLY = "half_yearly"
    FREQUENCY_YEARLY = "yearly"
    FREQUENCY_CHOICES = [
        (FREQUENCY_MONTHLY, "Monthly"),
        (FREQUENCY_QUARTERLY, "Quarterly"),
        (FREQUENCY_HALF_YEARLY, "Half-yearly"),
        (FREQUENCY_YEARLY, "Yearly"),
    ]

    client = models.ForeignKey("Client", on_delete=models.CASCADE, related_name="renewals")
    product_ref = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="renewal_entries")
    product_type = models.CharField(max_length=20, choices=PRODUCT_TYPE_CHOICES)
    product_name = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Required if product type is Other",
    )
    renewal_date = models.DateField()
    renewal_end_date = models.DateField(null=True, blank=True, db_index=True)
    frequency = models.CharField(max_length=15, choices=FREQUENCY_CHOICES)
    employee = models.ForeignKey("Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="renewals")
    premium_amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)], default=0)
    premium_collected_on = models.DateField(default=timezone.localdate, db_index=True)
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-renewal_date"]

    def clean(self):
        if self.product_type == self.PRODUCT_TYPE_OTHER and not (self.product_name or "").strip():
            from django.core.exceptions import ValidationError

            raise ValidationError({"product_name": "Product name is required when product type is Other."})
        if self.product_type != self.PRODUCT_TYPE_OTHER:
            self.product_name = None

    def __str__(self):
        product_label = self.product_ref.name if self.product_ref_id else self.get_product_type_display()
        return f"{self.client} - {product_label} - {self.renewal_date}"



# ---------- IncentiveRule (configurable in admin) ----------
class IncentiveRule(models.Model):
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
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Incentive Rule"
        verbose_name_plural = "Incentive Rules"

    def __str__(self):
        return f"{self.product}: {self.points_per_unit} pts per {self.unit_amount}"


class IncentiveSlab(models.Model):
    """Slab-based incentive tiers for products like Life Insurance.
    When cumulative monthly amount reaches `threshold`, the `payout` is awarded."""
    rule = models.ForeignKey(IncentiveRule, on_delete=models.CASCADE, related_name="slabs")
    threshold = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Cumulative monthly amount threshold"
    )
    payout = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text="Points/payout awarded when threshold is reached"
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

    def _effective_product_label(self):
        if self.product_ref_id:
            return self.product_ref.name
        return self.product

    def _active_campaign_product(self):
        """Return the single CampaignProduct covering this sale's product on its
        date, or None. Overlap prevention guarantees at most one match."""
        from .models import CampaignProduct  # avoid circular import

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
        from .models import CampaignSlab  # avoid circular import

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
        from .models import IncentiveRule, IncentiveSlab  # avoid circular import

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



class MonthlyIncentive(models.Model):
    """
    Snapshot of total points and total sales amount for each employee for a given year+month.
    """
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='monthly_incentives')
    year = models.IntegerField()
    month = models.IntegerField()
    total_points = models.DecimalField(max_digits=18, decimal_places=3, default=Decimal('0.000'))
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"{self.employee} - {self.year}-{str(self.month).zfill(2)} : {self.total_points} pts"



class Target(models.Model):
    TARGET_TYPE_CHOICES = [
        ("daily", "Daily"),
        ("monthly", "Monthly"),
    ]

    product = models.CharField(max_length=50)
    product_ref = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="targets")
    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("product", "target_type")  # ✅ one daily + one monthly per product

    def __str__(self):
        return f"{self.product} ({self.target_type})"


class BusinessTarget(models.Model):
    metric = models.CharField(max_length=100)
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    unit = models.CharField(max_length=50, blank=True, default="")
    start_date = models.DateField()
    end_date = models.DateField()
    active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-end_date", "-created_at"]

    def __str__(self):
        return f"{self.metric} {self.start_date}–{self.end_date}"


class MonthlyTargetHistory(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    product = models.CharField(max_length=50)
    product_ref = models.ForeignKey("Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="monthly_target_histories")
    year = models.IntegerField()
    month = models.IntegerField()
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    achieved_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    points_value = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))

    class Meta:
        unique_together = ("employee", "product", "year", "month")

    def __str__(self):
        return f"{self.employee} - {self.product} ({self.month}/{self.year})"


class EmployeeTarget(models.Model):
    """Per-employee monthly target for a product.

    Each employee carries a different competency/skill level, so admins enter a
    monthly target for each (employee, product) here. The daily target is derived
    by dividing the monthly value across the working days of the month
    (see ``daily_value``). Where no row exists for an employee+product, callers
    fall back to the product-wide baseline on :class:`Target`.

    These are per-head targets: the organisation-wide target for a product is the
    sum of every active employee's value, not a fixed pool being split.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="targets")
    product = models.CharField(max_length=50)
    product_ref = models.ForeignKey(
        "Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_targets"
    )
    target_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Monthly target for this employee on this product.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("employee", "product")
        ordering = ["employee__user__username", "product"]

    def __str__(self):
        return f"{self.employee} - {self.product}: {self.target_value}/mo"

    def daily_value(self, working_days):
        """Monthly target divided across ``working_days`` (>=1)."""
        wd = working_days if working_days and working_days > 0 else 1
        return (self.target_value or Decimal("0")) / Decimal(wd)


class ManagerAccessConfig(models.Model):
    """Singleton-style config describing which features managers can access."""

    allow_view_all_sales = models.BooleanField(default=True)
    allow_approve_sales = models.BooleanField(default=False)
    allow_edit_sales = models.BooleanField(default=False)
    allow_manage_incentives = models.BooleanField(default=False)
    allow_recalc_points = models.BooleanField(default=False)
    allow_client_analysis = models.BooleanField(default=False)
    allow_employee_performance = models.BooleanField(default=True)
    allow_lead_management = models.BooleanField(default=False)
    allow_calling_admin = models.BooleanField(default=False)
    allow_business_tracking = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Manager Access Config"

    @classmethod
    def current(cls):
        cfg, _ = cls.objects.get_or_create(id=1)
        return cfg


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


class CalendarEvent(models.Model):
    EVENT_TYPES = [
        ("call_followup", "Call Follow-up"),
        ("meeting", "Meeting"),
        ("task", "Task"),
        ("reminder", "Reminder"),
    ]

    employee = models.ForeignKey("Employee", on_delete=models.CASCADE, related_name="calendar_events")
    client = models.ForeignKey("clients.Client", on_delete=models.SET_NULL, null=True, blank=True, related_name="calendar_events")
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=EVENT_TYPES, default="task")
    scheduled_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    reminder_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("completed", "Completed"),
            ("rescheduled", "Rescheduled"),
            ("skipped", "Skipped")
        ],
        default="pending",
        db_index=True,
    )
    # set once send_followup_reminders has notified the owner at start time
    reminded = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.employee})"


class MessageTemplate(models.Model):
    name = models.CharField(max_length=120)
    content = models.TextField(help_text="Use any placeholders from the Client model like {{ name }}, {{ phone }}, {{ sip_amount }} etc.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def render(self, obj, extra_context=None):
        """
        Render the message with any placeholders present in 'content'.
        Automatically maps object attributes (e.g. from Client).
        """
        # Convert model instance to dict of all attributes
        context_data = {}

        # Add all field names + values from model (safe reflection)
        for field in obj._meta.get_fields():
            try:
                val = getattr(obj, field.name, "")
                # handle related fields (like mapped_to.user.username)
                if hasattr(val, "username"):
                    val = val.username
                context_data[field.name] = val
            except Exception:
                continue

        # Merge any extra values
        if extra_context:
            context_data.update(extra_context)

        # Replaces {{ variable }} patterns only — no tag execution, prevents injection.
        try:
            rendered = _TEMPLATE_VAR_RE.sub(
                lambda m: str(context_data.get(m.group(1).strip(), m.group(0))),
                self.content,
            )
            return strip_tags(rendered).strip()
        except Exception as e:
            logger.error("MessageTemplate render error: %s", e)
            return self.content


class MessageLog(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    template = models.ForeignKey(MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    client = models.ForeignKey('Client', null=True, blank=True, on_delete=models.SET_NULL)
    recipient_phone = models.CharField(max_length=32)
    message_text = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    error = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Message to {self.recipient_phone} [{self.status}]"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    related_sale = models.ForeignKey(
        "Sale", null=True, blank=True, on_delete=models.CASCADE
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"], name="notif_recip_read_idx"),
        ]

    def __str__(self):
        return f"{self.title} -> {self.recipient}"


class CallTrackingSettings(models.Model):
    """Singleton: admin-configured windows for call tracking and the follow-up
    popup. These are now independent — e.g. don't count calls on Sundays but
    still show the follow-up popup every day.

    Days are stored as comma-separated weekday numbers, 0=Mon … 6=Sun.
    """

    # ── Call tracking / analytics window ──
    enabled = models.BooleanField(default=True)
    work_start = models.TimeField(default=datetime_time(10, 0))
    work_end = models.TimeField(default=datetime_time(18, 0))
    work_days = models.CharField(
        max_length=20, default="0,1,2,3,4,5",
        help_text="Weekdays counted in analytics. 0=Mon … 6=Sun. Default Mon–Sat.",
    )

    # ── Follow-up popup window (independent of tracking) ──
    # 24×7 by default (owner decision 2026-07-14: a missed follow-up prompt is
    # worse than a late-night popup). Admin can still narrow it in App Settings.
    popup_enabled = models.BooleanField(default=True)
    popup_start = models.TimeField(default=datetime_time(0, 0))
    popup_end = models.TimeField(default=datetime_time(23, 59))
    popup_days = models.CharField(
        max_length=20, default="0,1,2,3,4,5,6",
        help_text="Weekdays the post-call popup appears. Default every day.",
    )
    # Which quick-timing chips the popup shows, comma-separated keys from
    # views.calls.FOLLOWUP_CATALOG in display order. Admin edits this from the
    # app Settings screen; the popup reads it via /api/calls/config/.
    popup_choices = models.CharField(
        max_length=250,
        default="10m,15m,30m,1h,2h,3h,4h,1d,5d,10d,1w,2w,1mo,2mo",
        help_text="Comma-separated quick-chip keys shown on the post-call popup.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Call Tracking Settings"
        verbose_name_plural = "Call Tracking Settings"

    def __str__(self):
        return f"Call tracking {self.work_start:%H:%M}–{self.work_end:%H:%M} ({'on' if self.enabled else 'off'})"

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @staticmethod
    def _parse_days(raw):
        out = []
        for part in (raw or "").split(","):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 6:
                out.append(int(part))
        return out

    def work_day_list(self):
        return self._parse_days(self.work_days)

    def popup_day_list(self):
        return self._parse_days(self.popup_days)

    def popup_choice_list(self):
        """Enabled quick-chip keys in display order (unvalidated — the view
        intersects with its catalog so stale keys degrade gracefully)."""
        return [p.strip() for p in (self.popup_choices or "").split(",") if p.strip()]

    @staticmethod
    def _to_django_week_days(day_list):
        """Map our 0=Mon…6=Sun to Django __week_day (1=Sun…7=Sat)."""
        # Mon(0)->2, Tue(1)->3, … Sat(5)->7, Sun(6)->1
        return [1 if d == 6 else d + 2 for d in day_list]

    def work_week_days_django(self):
        return self._to_django_week_days(self.work_day_list())

    def filter_work_window(self, qs, field="started_at"):
        """Restrict a CallLogEntry queryset to the tracking window
        (work hours + work days), evaluated in the project timezone."""
        return qs.filter(**{
            f"{field}__time__gte": self.work_start,
            f"{field}__time__lt": self.work_end,
            f"{field}__week_day__in": self.work_week_days_django(),
        })


class CallLogEntry(models.Model):
    """One phone call observed on an employee's device (synced by the app)."""

    DIRECTION_INCOMING = "incoming"
    DIRECTION_OUTGOING = "outgoing"
    DIRECTION_CHOICES = [
        (DIRECTION_INCOMING, "Incoming"),
        (DIRECTION_OUTGOING, "Outgoing"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="call_logs")
    phone = models.CharField(max_length=32, db_index=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    connected = models.BooleanField(default=False)
    duration_seconds = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(db_index=True)
    client = models.ForeignKey(
        "Client", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="call_logs", help_text="Auto-matched by phone number.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Dedup: the app may re-sync the same call.
        unique_together = [("employee", "phone", "started_at")]
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["employee", "-started_at"], name="call_emp_time_idx"),
        ]

    def __str__(self):
        return f"{self.employee} {self.direction} {self.phone} ({self.duration_seconds}s)"


class CallFollowUp(models.Model):
    """Follow-up on a phone call, scheduled from the post-call popup.
    A reminder push is sent at `scheduled_at`; tapping it dials the number."""

    STATUS_PENDING = "pending"
    STATUS_DONE = "done"
    STATUS_DISMISSED = "dismissed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_DONE, "Done"),
        (STATUS_DISMISSED, "Dismissed"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="call_followups")
    phone = models.CharField(max_length=32)
    client = models.ForeignKey(
        "Client", null=True, blank=True, on_delete=models.SET_NULL, related_name="call_followups"
    )
    scheduled_at = models.DateTimeField(db_index=True)
    note = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    reminded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "scheduled_at"]

    def __str__(self):
        return f"Follow-up {self.phone} @ {self.scheduled_at:%d-%b %H:%M} ({self.status})"


class AppDeviceStatus(models.Model):
    """Latest app/permission state per user, reported by the Android app on
    every launch. Lets admins see who hasn't granted call-tracking permissions
    (Call Analytics page shows the roster)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="app_device_status"
    )
    calls_granted = models.BooleanField(default=False)      # READ_PHONE_STATE + READ_CALL_LOG
    overlay_granted = models.BooleanField(default=False)    # display-over-other-apps (popup)
    notifications_granted = models.BooleanField(default=False)
    app_version = models.CharField(max_length=20, blank=True, default="")
    # Free-form popup/alarm health snapshot from the device: cached popup
    # config, SIM state, last popup shown/skipped + reason, alarm permissions.
    # Lets the admin see from Call Analytics WHY a device shows no popups.
    diagnostics = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App Device Status"
        verbose_name_plural = "App Device Statuses"

    def __str__(self):
        return f"{self.user.username} v{self.app_version} calls={self.calls_granted} overlay={self.overlay_granted}"


class PushDevice(models.Model):
    """An FCM device token belonging to a user, registered by the Android app.

    One user can have several devices. Tokens are upserted on app launch and
    pruned automatically when FCM reports them unregistered.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices"
    )
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=20, default="android")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.user.username} · {self.platform} · …{self.token[-12:]}"


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


# ═══════════════════════════════════════════════════════════════════════════
#  TASK MANAGEMENT MODULE  (replaces the external "Automate Tasks" tool)
#
#  Reuses CRM auth (Employee/User), the Notification pipeline (creating a
#  Notification auto-pushes to FCM via signals.push_on_notification), and the
#  Google Drive service for attachments/voice notes. Roles: admin = full
#  management, manager = team-wide, employee = own/delegated/subscribed.
# ═══════════════════════════════════════════════════════════════════════════


class TaskCategory(models.Model):
    """Admin-managed category for tasks (Sales, Operations, HR, …)."""

    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=7, default="#E5B740",
                             help_text="Hex color, e.g. #E5B740.")
    icon = models.CharField(max_length=40, default="bi-folder-fill",
                            help_text="Bootstrap icon class, e.g. 'bi-briefcase-fill'.")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Task category"
        verbose_name_plural = "Task categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Task(models.Model):
    """A unit of work assigned to an Employee, tracked from creation to done."""

    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_CRITICAL = "critical"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_MEDIUM, "Medium"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_CRITICAL, "Critical"),
    ]

    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_OVERDUE = "overdue"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_OVERDUE, "Overdue"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    # Statuses that are still "live" — eligible to flip to Overdue.
    OPEN_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_OVERDUE)

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(TaskCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="tasks")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES,
                                default=PRIORITY_MEDIUM)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES,
                              default=STATUS_PENDING, db_index=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="tasks_created")
    assigned_to = models.ForeignKey("Employee", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="tasks_assigned")
    # Optional client this task is about — surfaces the task on the client's
    # profile and gives the assignee a tap-to-call number on the task.
    client = models.ForeignKey("Client", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="tasks")

    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Accountability: the assignee taps "Acknowledge" (or changes status) to
    # confirm they've seen the task. tasks_ring_due re-rings unacknowledged
    # high/critical tasks every few hours (ack_last_rung_at is the marker).
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    ack_last_rung_at = models.DateTimeField(null=True, blank=True)
    # Exact due-time ring dispatched by tasks_ring_due (cleared on due change).
    due_alarm_sent_at = models.DateTimeField(null=True, blank=True)

    # Soft delete → recycle bin.
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    # Recurrence: repeat_rule mirrors the parent rule's frequency for display;
    # `recurring_rule` links generated instances back to their series.
    repeat_rule = models.CharField(
        max_length=20, blank=True,
        help_text="Recurrence frequency shown on the task: '', daily, weekly, monthly, custom.",
    )
    recurring_rule = models.ForeignKey(
        "RecurringTaskRule", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="instances",
    )

    # Reminder dispatch flags (set once by tasks_send_reminders so we don't
    # re-notify the same task).
    reminded_day_before = models.BooleanField(default=False)
    reminded_same_day = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assigned_to", "status"], name="task_assignee_status_idx"),
            models.Index(fields=["status", "due_date"], name="task_status_due_idx"),
            models.Index(fields=["is_deleted"], name="task_deleted_idx"),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"

    @property
    def due_at(self):
        """Aware datetime for the deadline (end-of-day if no time), or None."""
        if not self.due_date:
            return None
        from datetime import datetime as _dt
        naive = _dt.combine(self.due_date, self.due_time or datetime_time(23, 59))
        return timezone.make_aware(naive, timezone.get_current_timezone())

    @property
    def is_overdue(self):
        """True when the deadline has passed and the task is still open."""
        due = self.due_at
        return bool(due and self.status in self.OPEN_STATUSES and due < timezone.now())

    @property
    def checklist_percent(self):
        """Whole-number completion % of the checklist (0 when no items)."""
        items = self.checklist_items.all()
        total = len(items)
        if not total:
            return 0
        done = sum(1 for i in items if i.is_done)
        return round(done * 100 / total)

    def mark_completed(self, by_user=None):
        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])


class TaskSubscriber(models.Model):
    """An "In Loop" user — informed of progress but not responsible."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="subscribers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="task_subscriptions")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("task", "user")

    def __str__(self):
        return f"{self.user} ⊂ {self.task_id}"


class TaskChecklistItem(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist_items")
    title = models.CharField(max_length=255)
    is_done = models.BooleanField(default=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class TaskComment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on {self.task_id} by {self.author}"


class TaskAttachment(models.Model):
    """A file or voice note stored in Google Drive; served via a proxy view."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=255)
    mime = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    drive_file_id = models.CharField(max_length=128)
    drive_view_link = models.URLField(blank=True)
    is_voice_note = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.filename


class TaskActivity(models.Model):
    """Append-only audit trail for every meaningful change to a task."""

    CREATED = "created"
    ASSIGNED = "assigned"
    SUBSCRIBER_ADDED = "subscriber_added"
    PRIORITY_CHANGED = "priority_changed"
    CATEGORY_CHANGED = "category_changed"
    DESCRIPTION_UPDATED = "description_updated"
    CHECKLIST_UPDATED = "checklist_updated"
    ATTACHMENT_UPLOADED = "attachment_uploaded"
    VOICENOTE_UPLOADED = "voicenote_uploaded"
    COMMENT_ADDED = "comment_added"
    DUE_CHANGED = "due_changed"
    STATUS_CHANGED = "status_changed"
    COMPLETED = "completed"
    DELETED = "deleted"
    RESTORED = "restored"
    ACKNOWLEDGED = "acknowledged"
    ACTION_CHOICES = [
        (CREATED, "Task Created"),
        (ASSIGNED, "Task Assigned"),
        (SUBSCRIBER_ADDED, "Subscriber Added"),
        (PRIORITY_CHANGED, "Priority Changed"),
        (CATEGORY_CHANGED, "Category Changed"),
        (DESCRIPTION_UPDATED, "Description Updated"),
        (CHECKLIST_UPDATED, "Checklist Updated"),
        (ATTACHMENT_UPLOADED, "Attachment Uploaded"),
        (VOICENOTE_UPLOADED, "Voice Note Uploaded"),
        (COMMENT_ADDED, "Comment Added"),
        (DUE_CHANGED, "Due Date Changed"),
        (STATUS_CHANGED, "Status Changed"),
        (COMPLETED, "Completed"),
        (DELETED, "Deleted"),
        (RESTORED, "Restored"),
        (ACKNOWLEDGED, "Acknowledged"),
    ]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=25, choices=ACTION_CHOICES, db_index=True)
    detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Task activities"

    def __str__(self):
        return f"{self.get_action_display()} on {self.task_id}"


class TaskTemplate(models.Model):
    """Reusable task blueprint ("New client onboarding", "Month-end closing"):
    picking one in the Assign sheet prefills title, description, priority,
    category and checklist, instead of retyping the same procedure each time.
    Created by admins/managers ("Save as template" in the Assign sheet)."""

    name = models.CharField(max_length=120, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    priority = models.CharField(max_length=10, choices=Task.PRIORITY_CHOICES,
                                default=Task.PRIORITY_MEDIUM)
    category = models.ForeignKey(TaskCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    checklist = models.TextField(blank=True, default="",
                                 help_text="One checklist item per line.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def checklist_items(self):
        return [line.strip() for line in (self.checklist or "").splitlines() if line.strip()]


class RecurringTaskRule(models.Model):
    """A template + schedule that spawns Task instances (daily/weekly/monthly).

    The first instance is created immediately when a recurring task is assigned;
    subsequent ones are generated by the ``tasks_generate_recurring`` cron.
    """

    FREQ_DAILY = "daily"
    FREQ_WEEKLY = "weekly"
    FREQ_MONTHLY = "monthly"
    FREQ_CUSTOM = "custom"
    FREQ_CHOICES = [
        (FREQ_DAILY, "Daily"),
        (FREQ_WEEKLY, "Weekly"),
        (FREQ_MONTHLY, "Monthly"),
        (FREQ_CUSTOM, "Custom"),
    ]

    END_NEVER = "never"
    END_ON = "on_date"
    END_AFTER = "after_n"
    END_CHOICES = [
        (END_NEVER, "Never"),
        (END_ON, "On date"),
        (END_AFTER, "After N occurrences"),
    ]

    # ── Template for each generated task ──
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(TaskCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="+")
    priority = models.CharField(max_length=10, choices=Task.PRIORITY_CHOICES,
                                default=Task.PRIORITY_MEDIUM)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    assigned_to = models.ForeignKey("Employee", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    due_time = models.TimeField(null=True, blank=True)
    checklist_template = models.TextField(
        blank=True, help_text="One checklist item per line; copied onto each instance.")
    subscriber_ids = models.CharField(
        max_length=255, blank=True, help_text="CSV of User ids added In-Loop to each instance.")

    # ── Schedule ──
    frequency = models.CharField(max_length=10, choices=FREQ_CHOICES, default=FREQ_DAILY)
    interval = models.PositiveIntegerField(default=1, help_text="Every N days/weeks/months.")
    weekdays = models.CharField(
        max_length=20, blank=True,
        help_text="For weekly/custom: CSV weekday numbers, 0=Mon … 6=Sun.")
    start_date = models.DateField()
    end_type = models.CharField(max_length=10, choices=END_CHOICES, default=END_NEVER)
    end_date = models.DateField(null=True, blank=True)
    max_occurrences = models.PositiveIntegerField(null=True, blank=True)

    # ── State ──
    occurrences_created = models.PositiveIntegerField(default=0)
    last_generated_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_frequency_display()} · {self.title}"

    def weekday_list(self):
        out = []
        for part in (self.weekdays or "").split(","):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 6:
                out.append(int(part))
        return out

    def subscriber_id_list(self):
        out = []
        for part in (self.subscriber_ids or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out


class TaskReminderSetting(models.Model):
    """Singleton: admin-configured default reminder rules for tasks."""

    remind_day_before = models.BooleanField(default=True)
    remind_same_day = models.BooleanField(default=True)
    same_day_hour = models.PositiveSmallIntegerField(
        default=9, help_text="Hour (0-23) to send the same-day / day-before reminder.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Task reminder settings"
        verbose_name_plural = "Task reminder settings"

    def __str__(self):
        return "Task reminder settings"

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class NotificationPreference(models.Model):
    """Per-user toggles for which task notifications a user receives.

    Absence of a row means "all on" — the default. Field names match the event
    keys used by ``services.tasks.user_wants``.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="task_notification_pref")
    notify_assigned = models.BooleanField(default=True)
    notify_status_changed = models.BooleanField(default=True)
    notify_due_changed = models.BooleanField(default=True)
    notify_priority_changed = models.BooleanField(default=True)
    notify_comment_added = models.BooleanField(default=True)
    notify_checklist_updated = models.BooleanField(default=True)
    notify_attachment_added = models.BooleanField(default=True)
    notify_voicenote_added = models.BooleanField(default=True)
    notify_completed = models.BooleanField(default=True)
    notify_subscriber_added = models.BooleanField(default=True)
    notify_overdue = models.BooleanField(default=True)
    notify_recurring_created = models.BooleanField(default=True)

    def __str__(self):
        return f"Notification prefs for {self.user}"


class SavedTaskFilter(models.Model):
    """A named, per-user shortcut that stores a task-list query string."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="saved_task_filters")
    name = models.CharField(max_length=80)
    query_string = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("user", "name")

    def __str__(self):
        return self.name


# ═══════════════════════════════════════════════════════════════════════════
#  BUSINESS LINKS MODULE  (replaces the external "Automate Links" tool)
# ═══════════════════════════════════════════════════════════════════════════


class LinkCategory(models.Model):
    """A folder of business links (Sales, Operations, HR, …)."""

    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=7, default="#2563eb")
    icon = models.CharField(max_length=40, default="bi-link-45deg")
    description = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Link categories"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Link(models.Model):
    """A single business URL bookmark shared across the team."""

    title = models.CharField(max_length=200)
    url = models.URLField(max_length=500)
    description = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(LinkCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="links")
    icon = models.CharField(max_length=40, blank=True,
                            help_text="Optional Bootstrap icon class; else the category icon is used.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="links_created")
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "title"]
        indexes = [models.Index(fields=["category", "display_order"], name="link_cat_order_idx")]

    def __str__(self):
        return self.title


class LinkFavorite(models.Model):
    """Per-user favorite marker for a link."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="link_favorites")
    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "link")

    def __str__(self):
        return f"{self.user} ★ {self.link_id}"


# ───────────────────────── Mutual Funds (RTA feeds) ─────────────────────────
# Free CAMS/KFintech distributor mailback files imported into the CRM so client
# folios and transactions stay current. The firm operates under two codes (the
# NJ-routed ARN and the direct/NSE ARN) — ArnAccount models each of them and
# every imported row is attributed to one.

RTA_CAMS = "CAMS"
RTA_KFIN = "KFIN"
RTA_CHOICES = [(RTA_CAMS, "CAMS"), (RTA_KFIN, "KFintech")]


def normalize_broker_code(raw):
    """Uppercase alphanumerics only: 'ARN-152880 ' → 'ARN152880'."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


class ArnAccount(models.Model):
    """One distribution code the firm transacts under.

    ``arn_code`` is matched against the broker/ARN column of RTA files.
    ``sub_broker_code``, when set, must ALSO match the sub-broker column —
    this is how NJ-routed business (NJ's ARN + our sub-broker code) is told
    apart from NJ's other partners.
    """

    label = models.CharField(max_length=80, unique=True, help_text="e.g. 'Direct (NSE)' or 'NJ'")
    arn_code = models.CharField(max_length=40, help_text="As it appears in RTA files, e.g. ARN-152880")
    sub_broker_code = models.CharField(max_length=40, blank=True, default="",
                                       help_text="Required for platform-routed business (e.g. NJ partner code)")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return f"{self.label} ({self.arn_code})"

    def matches(self, broker_raw, sub_broker_raw=""):
        if normalize_broker_code(broker_raw) != normalize_broker_code(self.arn_code):
            return False
        if self.sub_broker_code:
            return normalize_broker_code(sub_broker_raw) == normalize_broker_code(self.sub_broker_code)
        return True

    @classmethod
    def resolve(cls, broker_raw, sub_broker_raw="", accounts=None):
        """Best ArnAccount for a file row; sub-broker matches beat plain ones."""
        candidates = [a for a in (accounts if accounts is not None else cls.objects.filter(is_active=True))
                      if a.matches(broker_raw, sub_broker_raw)]
        if not candidates:
            return None
        candidates.sort(key=lambda a: (0 if a.sub_broker_code else 1))
        return candidates[0]


class MutualFundFolio(models.Model):
    """A client's folio at an AMC, as reported by the RTA feeds."""

    folio_number = models.CharField(max_length=40, db_index=True)
    amc_name = models.CharField(max_length=120, blank=True, default="")
    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    arn = models.ForeignKey(ArnAccount, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="folios")
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="mf_folios")
    investor_name = models.CharField(max_length=200, blank=True, default="")
    pan = models.CharField(max_length=20, blank=True, default="", db_index=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("folio_number", "amc_name")
        ordering = ["investor_name", "folio_number"]

    def __str__(self):
        return f"{self.folio_number} · {self.amc_name or self.rta}"


class MutualFundTransaction(models.Model):
    """One row of an RTA transaction feed (WBR2 / KFintech equivalent)."""

    folio = models.ForeignKey(MutualFundFolio, on_delete=models.CASCADE, related_name="transactions")
    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    scheme_name = models.CharField(max_length=200, blank=True, default="")
    txn_type = models.CharField(max_length=80, blank=True, default="")
    txn_number = models.CharField(max_length=60, blank=True, default="")
    trade_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    units = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    nav = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    arn = models.ForeignKey(ArnAccount, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="mf_transactions")
    broker_code = models.CharField(max_length=40, blank=True, default="")
    sub_broker_code = models.CharField(max_length=40, blank=True, default="")
    # sha1 over the identifying columns — makes re-imported files no-ops.
    dedupe_key = models.CharField(max_length=40, unique=True)
    source_import = models.ForeignKey("RTAFeedImport", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="transactions")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_date", "-id"]
        indexes = [
            models.Index(fields=["trade_date"], name="mftxn_trade_date_idx"),
            models.Index(fields=["arn", "trade_date"], name="mftxn_arn_date_idx"),
        ]

    def __str__(self):
        return f"{self.folio.folio_number} {self.txn_type} ₹{self.amount} on {self.trade_date}"


class RTAFeedImport(models.Model):
    """Audit log: one processed feed file (from the mailbox or manual upload)."""

    SOURCE_EMAIL = "email"
    SOURCE_UPLOAD = "upload"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"

    rta = models.CharField(max_length=8, choices=RTA_CHOICES, blank=True, default="")
    source = models.CharField(max_length=10, choices=[(SOURCE_EMAIL, "Email"), (SOURCE_UPLOAD, "Upload")])
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=10, default=STATUS_PROCESSED, choices=[
        (STATUS_PROCESSED, "Processed"), (STATUS_FAILED, "Failed"), (STATUS_SKIPPED, "Skipped"),
    ])
    rows_total = models.IntegerField(default=0)
    rows_imported = models.IntegerField(default=0)
    rows_duplicate = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    folios_created = models.IntegerField(default=0)
    clients_linked = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.file_name} [{self.status}]"

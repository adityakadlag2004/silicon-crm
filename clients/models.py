import logging
import re

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models, transaction
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

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True)
    domain = models.CharField(max_length=20, choices=DOMAIN_CHOICES, default=DOMAIN_BOTH)
    display_order = models.IntegerField(default=0)
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

    def save(self, *args, **kwargs):
        # Auto-generate sequential id if not set
        is_new = self._state.adding
        if self.id is None:
            with transaction.atomic():
                max_id = Client.objects.aggregate(max_id=Max('id'))['max_id'] or 0
                self.id = max_id + 1
        else:
            # Ensure edited_at is set at least once after the first edit so
            # the "Show Edited" filter can surface historical edits.
            if self.edited_at is None and not is_new:
                self.edited_at = timezone.now()
        # Normalize lumsum investment to 0 if missing
        if self.lumsum_investment is None:
            self.lumsum_investment = Decimal("0.00")
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
            self.save(update_fields=['mapped_to'])

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
    employee = models.ForeignKey("Employee", on_delete=models.CASCADE, related_name="sales")
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

    def compute_points(self):
        """Compute points based on IncentiveRule + IncentiveSlab in DB"""
        from .models import IncentiveRule, IncentiveSlab  # avoid circular import

        product_label = self._effective_product_label()

        if self._is_health_product() and self.policy_type == self.POLICY_TYPE_PORT:
            self.points = Decimal("0.000")
            self.incentive_amount = Decimal("0.00")
            return

        try:
            rule_qs = IncentiveRule.objects.filter(active=True)
            if self.product_ref_id:
                rule_qs = rule_qs.filter(product_ref=self.product_ref)
            else:
                rule_qs = rule_qs.filter(product=product_label)
            rule = rule_qs.get()

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
                qs = Sale.objects.filter(employee=self.employee, date__year=sale_year, date__month=sale_month)
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
        on_delete=models.CASCADE,
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


# ────────────────────────────────────────────────────────────────────────────
# Lead Records: lightweight spreadsheet system for tracking incoming leads
# per product / source. Each LeadSheet has its own user-defined columns.
# ────────────────────────────────────────────────────────────────────────────


class LeadSheet(models.Model):
    """A spreadsheet-like collection of leads, e.g. 'Health Insurance Q3 leads'.

    Access model:
      - is_private=True              → only owner + admins/superusers
      - is_private=False, no shared  → all employees (firm-wide)
      - is_private=False, shared set → owner + admins + employees in shared_with
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    product = models.ForeignKey(
        "Product", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="lead_sheets",
        help_text="Optional: which product this sheet is tracking leads for.",
    )
    owner = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="owned_lead_sheets",
    )
    is_private = models.BooleanField(
        default=False,
        help_text="If true, only owner + admins can view. Otherwise see shared_with.",
    )
    shared_with = models.ManyToManyField(
        "Employee", blank=True, related_name="shared_lead_sheets",
        help_text="If non-empty (and not private), these employees + owner + admins can view.",
    )
    archived = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "archived"], name="ls_owner_arch_idx"),
        ]

    def __str__(self):
        return self.name

    def can_view(self, user):
        """True if the user can view this sheet given the access model above."""
        if user.is_superuser:
            return True
        emp = getattr(user, "employee", None)
        if not emp:
            return False
        if emp.role == "admin":
            return True
        if self.is_private:
            return self.owner_id == emp.id
        # not private — open to firm or to listed employees
        if not self.shared_with.exists():
            return True
        return self.owner_id == emp.id or self.shared_with.filter(pk=emp.pk).exists()

    def can_edit(self, user):
        """For now: edit permission = view permission. Tighten later if needed."""
        return self.can_view(user)


class LeadSheetColumn(models.Model):
    """Column definition for a LeadSheet. Records store values keyed by `field_key`."""
    TYPE_TEXT = "text"
    TYPE_NUMBER = "number"
    TYPE_DATE = "date"
    TYPE_PHONE = "phone"
    TYPE_EMAIL = "email"
    TYPE_SELECT = "select"
    TYPE_STATUS = "status"
    TYPE_CHOICES = [
        (TYPE_TEXT, "Text"),
        (TYPE_NUMBER, "Number"),
        (TYPE_DATE, "Date"),
        (TYPE_PHONE, "Phone"),
        (TYPE_EMAIL, "Email"),
        (TYPE_SELECT, "Dropdown"),
        (TYPE_STATUS, "Status badge"),
    ]

    sheet = models.ForeignKey(LeadSheet, related_name="columns", on_delete=models.CASCADE)
    name = models.CharField(max_length=100, help_text="Display name shown in the column header.")
    field_key = models.SlugField(max_length=60, help_text="Internal key — record values are stored under this.")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_TEXT)
    options = models.JSONField(
        default=list, blank=True,
        help_text="For 'select' or 'status' types: list of allowed values.",
    )
    required = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["display_order", "id"]
        unique_together = [("sheet", "field_key")]

    def __str__(self):
        return f"{self.sheet.name} · {self.name}"


class LeadSheetRecord(models.Model):
    """One row in a LeadSheet. Values stored in JSONB keyed by column.field_key."""
    sheet = models.ForeignKey(LeadSheet, related_name="records", on_delete=models.CASCADE)
    values = models.JSONField(default=dict, blank=True)
    # Free-form short labels (e.g. "hot", "callback", "vip"). Each sheet's tag set
    # is the union of tags across its records — no separate tag table needed.
    tags = models.JSONField(default=list, blank=True)
    # Auto-assigned (round-robin) when the sheet is shared with multiple employees.
    # Null for firm-wide / private sheets unless explicitly set.
    assigned_to = models.ForeignKey(
        "Employee", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_lead_records", db_index=True,
    )
    converted_client = models.ForeignKey(
        "Client", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="originating_lead_records",
        help_text="Set when this row has been converted into a Client.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["sheet", "-created_at"], name="lsr_sheet_time_idx"),
        ]

    def __str__(self):
        return f"{self.sheet.name} row #{self.pk}"


class LeadSheetFollowUp(models.Model):
    """A scheduled follow-up against a LeadSheetRecord — same shape as
    LeadFollowUp on the Lead model, but separate so the two systems can
    evolve independently."""
    record = models.ForeignKey(
        LeadSheetRecord, related_name="followups", on_delete=models.CASCADE,
    )
    scheduled_at = models.DateTimeField(db_index=True)
    note = models.TextField(blank=True, default="")
    completed = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["completed", "scheduled_at"]
        indexes = [
            models.Index(fields=["record", "-scheduled_at"], name="lsfu_record_time_idx"),
        ]

    def __str__(self):
        return f"Follow-up for {self.record} at {self.scheduled_at}"


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

"""Clients, mapping audit trail, renewals."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import connection
from django.db import models
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from .hr import Employee


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

    # Household this client belongs to (optional — plenty of clients stand alone).
    family = models.ForeignKey("Family", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="members")

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

    @property
    def insurance_kind(self):
        """'health' / 'life' / 'other' — derived from product_ref first, since
        product_type wasn't always persisted on historical rows."""
        if self.product_ref_id:
            code = (self.product_ref.code or "").upper()
            if code == "HEALTH_INS":
                return "health"
            if code == "LIFE_INS":
                return "life"
        if self.product_type == self.PRODUCT_TYPE_HEALTH:
            return "health"
        if self.product_type == self.PRODUCT_TYPE_LIFE:
            return "life"
        return "other"

    # DB-level classifiers matching insurance_kind, for aggregate queries.
    @staticmethod
    def kind_q(kind):
        from django.db.models import Q
        if kind == "health":
            return Q(product_ref__code="HEALTH_INS") | Q(product_type="health_insurance")
        if kind == "life":
            return Q(product_ref__code="LIFE_INS") | Q(product_type="life_insurance")
        return ~(Q(product_ref__code__in=["HEALTH_INS", "LIFE_INS"])
                 | Q(product_type__in=["health_insurance", "life_insurance"]))

    def __str__(self):
        product_label = self.product_ref.name if self.product_ref_id else self.get_product_type_display()
        return f"{self.client} - {product_label} - {self.renewal_date}"


class Family(models.Model):
    """A household — several Clients who are reviewed and served together.

    MFD practice is family-first: an RM meets the family, not each folio
    holder separately. Grouping clients lets the back office see combined AUM
    and band the household (Super HNI, HNI, …) the way the industry does.
    """

    # Bands are (label, floor in rupees), richest first — see `category`.
    CATEGORY_BANDS = [
        ("Super HNI (Above 5 Cr)", 50_000_000),
        ("HNI (1 Cr to 5 Cr)", 10_000_000),
        ("Upper Middle (50 L to 1 Cr)", 5_000_000),
        ("Middle (Below 50 Lakh)", 0),
    ]

    name = models.CharField(max_length=200, db_index=True)
    code = models.CharField(max_length=20, unique=True,
                            help_text="Short household code, e.g. A001.")
    head = models.ForeignKey("Client", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="heads_family",
                             help_text="Primary investor / group head.")
    relationship_manager = models.ForeignKey("Employee", null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name="families")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Families"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def aum(self):
        """Combined lumpsum + PMS across the household."""
        from django.db.models import Sum
        agg = self.members.aggregate(
            lump=Sum("lumsum_investment"), pms=Sum("pms_amount"))
        return (agg["lump"] or 0) + (agg["pms"] or 0)

    @property
    def category(self):
        """Wealth band for the household's combined AUM."""
        total = self.aum
        for label, floor in self.CATEGORY_BANDS:
            if total >= floor:
                return label
        return self.CATEGORY_BANDS[-1][0]

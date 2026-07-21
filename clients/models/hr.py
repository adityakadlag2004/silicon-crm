"""People: employees, their monthly targets, manager access rights."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models


class Employee(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        EMPLOYEE = "employee", "Employee"

    class Marital(models.TextChoices):
        SINGLE = "single", "Single"
        MARRIED = "married", "Married"

    class Domain(models.TextChoices):
        SALES = "sales", "Sales"
        SERVICE = "service", "Service / Operations"
        BACK_OFFICE = "back_office", "Back Office"
        COMPLIANCE = "compliance", "Compliance"
        MARKETING = "marketing", "Marketing"
        MANAGEMENT = "management", "Management"

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=50, choices=Role.choices)
    active = models.BooleanField(default=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employee_number = models.CharField(max_length=50, unique=True, null=True, blank=True)

    # ── Who they are ──────────────────────────────────────────────────
    # Names live here rather than on User: User.first_name is capped at 150
    # and has no room for a middle name, and we want the CRM to own this.
    first_name = models.CharField(max_length=60, blank=True)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    personal_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    marital_status = models.CharField(max_length=10, choices=Marital.choices, blank=True)

    # ── The job ───────────────────────────────────────────────────────
    position = models.CharField(max_length=80, blank=True,
                                help_text="Job title, e.g. Relationship Manager.")
    domain = models.CharField(max_length=20, choices=Domain.choices, blank=True,
                              help_text="Which side of the business they work in.")
    joining_date = models.DateField(null=True, blank=True)
    reports_to = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="reportees")
    qualification = models.CharField(max_length=120, blank=True)
    skills = models.CharField(max_length=255, blank=True,
                              help_text="Comma-separated, e.g. 'MFD, NISM-VA, Excel'.")

    # ── In case of emergency ──────────────────────────────────────────
    emergency_contact_name = models.CharField(max_length=80, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)

    notes = models.TextField(blank=True)
    profile_updated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.full_name or self.user.username

    # ── Derived ───────────────────────────────────────────────────────

    @property
    def full_name(self):
        """Preferred display name: our own fields, else User's, else login."""
        parts = [self.first_name, self.middle_name, self.last_name]
        ours = " ".join(p for p in parts if p).strip()
        return ours or self.user.get_full_name() or self.user.username

    @property
    def short_name(self):
        return self.first_name or self.user.first_name or self.user.username

    @property
    def tenure_months(self):
        """Months served here. 0 when the joining date is unknown."""
        if not self.joining_date:
            return 0
        from django.utils import timezone
        today = timezone.localdate()
        months = (today.year - self.joining_date.year) * 12 + today.month - self.joining_date.month
        if today.day < self.joining_date.day:
            months -= 1
        return max(months, 0)

    @staticmethod
    def humanise_months(months):
        """36 -> '3y', 14 -> '1y 2m', 5 -> '5m', 0 -> '—'."""
        if not months:
            return "—"
        years, rem = divmod(int(months), 12)
        if years and rem:
            return f"{years}y {rem}m"
        return f"{years}y" if years else f"{rem}m"

    @property
    def tenure_display(self):
        return self.humanise_months(self.tenure_months)

    def skill_list(self):
        return [s.strip() for s in (self.skills or "").split(",") if s.strip()]

    # ── Profile completeness ──────────────────────────────────────────
    # Drives the "your profile is incomplete" prompt on the employee
    # dashboard. Only fields the employee can reasonably fill themselves
    # are counted — salary and employee_number are the admin's job.
    SELF_SERVICE_FIELDS = [
        ("first_name", "First name"),
        ("last_name", "Last name"),
        ("date_of_birth", "Date of birth"),
        ("phone", "Phone number"),
        ("personal_email", "Personal email"),
        ("address", "Address"),
        ("qualification", "Qualification"),
        ("emergency_contact_name", "Emergency contact name"),
        ("emergency_contact_phone", "Emergency contact phone"),
    ]
    # Admin-owned fields, reported separately so the admin sees their own gaps.
    ADMIN_FIELDS = [
        ("joining_date", "Joining date"),
        ("position", "Position"),
        ("domain", "Domain"),
    ]

    def missing_fields(self, include_admin=False):
        """[(field, label)] the employee still needs to fill in."""
        checks = list(self.SELF_SERVICE_FIELDS)
        if include_admin:
            checks += self.ADMIN_FIELDS
        return [(f, label) for f, label in checks if not getattr(self, f, None)]

    @property
    def profile_completeness(self):
        """Whole-number % of self-service fields filled."""
        total = len(self.SELF_SERVICE_FIELDS)
        done = total - len(self.missing_fields())
        return round(done * 100 / total) if total else 100

    @property
    def profile_is_complete(self):
        return not self.missing_fields()


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


class EmployeeMilestone(models.Model):
    """Something worth marking in a colleague's year.

    The point of this model is deliberately non-financial: birthdays, work
    anniversaries, qualifications passed, a target beaten, a long service
    mark. The admin gets nudged about these the same way the CRM nudges about
    overdue tasks, so recognition is a scheduled habit rather than something
    remembered late.

    Date-driven ones (birthday, anniversary) are generated from the employee
    record by the ``employee_milestones`` cron; achievements are entered by
    hand.
    """

    class Kind(models.TextChoices):
        BIRTHDAY = "birthday", "Birthday"
        WORK_ANNIVERSARY = "work_anniversary", "Work Anniversary"
        LONG_SERVICE = "long_service", "Long Service"
        QUALIFICATION = "qualification", "Qualification / Certification"
        TARGET_BEATEN = "target_beaten", "Target Beaten"
        PROMOTION = "promotion", "Promotion"
        APPRECIATION = "appreciation", "Appreciation"

    # Kinds the cron derives from dates; the rest are entered by a human.
    AUTO_KINDS = (Kind.BIRTHDAY, Kind.WORK_ANNIVERSARY, Kind.LONG_SERVICE)

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="milestones")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    occurs_on = models.DateField(db_index=True)
    title = models.CharField(max_length=160)
    detail = models.TextField(blank=True)
    # Years being marked, for anniversaries — "3 years with the firm".
    years = models.PositiveIntegerField(null=True, blank=True)

    # Recognition state: raised → acknowledged by the admin → celebrated.
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    celebrated_at = models.DateTimeField(null=True, blank=True)
    celebrated_by = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="+")
    celebration_note = models.TextField(blank=True)

    created_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurs_on", "employee__user__username"]
        # One birthday per person per year, not one per cron run.
        unique_together = ("employee", "kind", "occurs_on")

    def __str__(self):
        return f"{self.get_kind_display()} · {self.employee} · {self.occurs_on}"

    @property
    def is_celebrated(self):
        return self.celebrated_at is not None

    @property
    def days_away(self):
        """Negative when it has passed, 0 today, positive when upcoming."""
        from django.utils import timezone
        return (self.occurs_on - timezone.localdate()).days

    @property
    def is_overdue(self):
        """Passed without being celebrated — what the admin nudge is for."""
        return self.days_away < 0 and not self.is_celebrated

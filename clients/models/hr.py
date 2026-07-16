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

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=50, choices=Role.choices)
    active = models.BooleanField(default=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employee_number = models.CharField(max_length=50, unique=True, null=True, blank=True)

    def __str__(self):
        return self.user.username


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

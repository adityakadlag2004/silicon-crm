"""Firm-level and legacy target models."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from .hr import Employee


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

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum, Q
from .models import Sale, Client, Notification, Employee, Product
from django.contrib.auth import get_user_model
from django.urls import reverse

@receiver([post_save, post_delete], sender=Sale)
def update_client_status(sender, instance, **kwargs):
    client = instance.client
    sales = Sale.objects.filter(client=client)

    code_to_name = {p.code: p.name for p in Product.objects.all().only("code", "name")}

    def _sum_amount(product_code, fallback_name, field_name):
        product_name = code_to_name.get(product_code, fallback_name)
        amount = sales.filter(Q(product_ref__code=product_code) | Q(product=product_name)).aggregate(total=Sum(field_name))["total"]
        return amount or 0

    client.sip_amount = _sum_amount("SIP", "SIP", "amount")
    client.life_cover = _sum_amount("LIFE_INS", "Life Insurance", "cover_amount")
    client.health_cover = _sum_amount("HEALTH_INS", "Health Insurance", "cover_amount")
    client.motor_insured_value = _sum_amount("MOTOR_INS", "Motor Insurance", "amount")
    client.pms_amount = _sum_amount("PMS", "PMS", "amount")

    client.sip_status = client.sip_amount > 0
    client.life_status = client.life_cover > 0
    client.health_status = client.health_cover > 0
    client.motor_status = client.motor_insured_value > 0
    client.pms_status = client.pms_amount > 0

    client.save()


@receiver(post_save, sender=Sale)
def notify_admins_on_sale(sender, instance, created, **kwargs):
    if not created:
        return

    User = get_user_model()
    try:
        dashboard_url = reverse("clients:admin_dashboard")
    except Exception:
        dashboard_url = ""

    employee_name = getattr(instance.employee, "user", None)
    if employee_name and hasattr(employee_name, "username"):
        employee_name = employee_name.username
    else:
        employee_name = str(instance.employee)

    client_name = getattr(instance.client, "name", str(instance.client))
    amount_display = f"₹{instance.amount}"

    body = (
        f"{employee_name} logged a {instance.product} sale of {amount_display} "
        f"for client {client_name}."
    )

    admin_users = set(User.objects.filter(is_superuser=True))
    admin_users.update(
        User.objects.filter(employee__role="admin")
    )

    for admin_user in admin_users:
        Notification.objects.create(
            recipient=admin_user,
            title="New sale recorded",
            body=body,
            link=dashboard_url,
            related_sale=instance,
        )

from django.db.models import Sum
from django.utils.timezone import now
from django.core.cache import cache
import logging

from .models import Employee, Sale, Target, MonthlyTargetHistory

logger = logging.getLogger(__name__)

def close_month_targets(year: int, month: int, *, dry_run=False):
    """
    Close the month for year/month by storing employee performance vs target.
    Called by the 'close_month' management command via cron (see CRONJOBS in settings).
    """
    monthly_targets = Target.objects.filter(target_type="monthly")
    employees = Employee.objects.all()

    for emp in employees:
        month_sales = (
            Sale.objects.filter(employee=emp, date__year=year, date__month=month)
            .values("product").annotate(total=Sum("amount"))
        )
        month_sales_dict = {s["product"]: s["total"] for s in month_sales}

        for target in monthly_targets:
            achieved = month_sales_dict.get(target.product, 0) or 0
            MonthlyTargetHistory.objects.update_or_create(
                employee=emp,
                product=target.product,
                year=year,
                month=month,
                defaults={
                    "target_value": target.target_value,
                    "achieved_value": achieved,
                },
            )

    if dry_run:
        logger.info("Dry run complete for %s/%s", month, year)

# NOTE: The request_started signal handler (run_monthly_close) has been removed.
# Monthly close is handled by the 'close_month' management command via CRONJOBS in settings.py.
# This avoids adding overhead to every HTTP request on the 1st of each month.

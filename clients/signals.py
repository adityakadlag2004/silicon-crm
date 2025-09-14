from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum
from .models import Sale, Client

@receiver([post_save, post_delete], sender=Sale)
def update_client_status(sender, instance, **kwargs):
    client = instance.client
    sales = Sale.objects.filter(client=client)

    client.sip_amount = sales.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    client.life_cover = sales.filter(product="Life Insurance").aggregate(total=Sum("cover_amount"))["total"] or 0
    client.health_cover = sales.filter(product="Health Insurance").aggregate(total=Sum("cover_amount"))["total"] or 0
    client.motor_insured_value = sales.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.pms_amount = sales.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    client.sip_status = client.sip_amount > 0
    client.life_status = client.life_cover > 0
    client.health_status = client.health_cover > 0
    client.motor_status = client.motor_insured_value > 0
    client.pms_status = client.pms_amount > 0

    client.save()

from django.db.models import Sum
from django.utils.timezone import now
from django.core.cache import cache
from django.core.signals import request_started
from django.dispatch import receiver
import logging

from .models import Employee, Sale, Target, MonthlyTargetHistory

logger = logging.getLogger(__name__)

def close_month_targets(year: int, month: int, *, dry_run=False):
    """
    Close the month for year/month by storing employee performance vs target.
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

@receiver(request_started)
def run_monthly_close(sender, **kwargs):
    today = now().date()
    if today.day != 1:
        return

    # figure out which month to close (previous month)
    if today.month == 1:
        prev_month = 12
        prev_year = today.year - 1
    else:
        prev_month = today.month - 1
        prev_year = today.year

    cache_key = f"monthly_close_done_{prev_year}_{prev_month}"

    if cache.get(cache_key):
        return

    exists = MonthlyTargetHistory.objects.filter(year=prev_year, month=prev_month).exists()
    if exists:
        cache.set(cache_key, True, 60 * 60 * 36)
        return

    try:
        close_month_targets(prev_year, prev_month, dry_run=False)
        cache.set(cache_key, True, 60 * 60 * 36)
        logger.info("Closed monthly targets for %s/%s", prev_month, prev_year)
    except Exception as e:
        logger.exception("Error running monthly close: %s", e)

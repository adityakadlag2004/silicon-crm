from django.core.management.base import BaseCommand
from django.utils.timezone import now
from django.db.models import Sum
from clients.models import Employee, Sale, Target, MonthlyTargetHistory

class Command(BaseCommand):
    help = "Close the previous month and store employee performance into MonthlyTargetHistory"

    def handle(self, *args, **kwargs):
        today = now().date()

        # figure out previous month
        if today.month == 1:
            year = today.year - 1
            month = 12
        else:
            year = today.year
            month = today.month - 1

        monthly_targets = Target.objects.filter(target_type="monthly")

        for emp in Employee.objects.all():
            # Employee's sales in the previous month
            month_sales = Sale.objects.filter(
                employee=emp,
                date__year=year,
                date__month=month
            ).values("product").annotate(total=Sum("amount"))

            month_sales_dict = {s["product"]: s["total"] for s in month_sales}

            for target in monthly_targets:
                achieved = month_sales_dict.get(target.product, 0)

                MonthlyTargetHistory.objects.update_or_create(
                    employee=emp,
                    product=target.product,
                    year=year,
                    month=month,
                    defaults={
                        "target_value": target.target_value,
                        "achieved_value": achieved,
                    }
                )

        self.stdout.write(self.style.SUCCESS(f"Monthly targets closed for {month}/{year}"))

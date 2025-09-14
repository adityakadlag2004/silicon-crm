from django.core.management.base import BaseCommand
from django.utils.timezone import now
from django.db.models import Sum
from clients.models import Employee, Sale, Target, MonthlyTargetHistory


class Command(BaseCommand):
    help = "Close the previous month and store employee performance into MonthlyTargetHistory"

    def handle(self, *args, **kwargs):
        today = now().date()

        # Figure out previous month
        if today.month == 1:
            year = today.year - 1
            month = 12
        else:
            year = today.year
            month = today.month - 1

        # Fetch monthly targets (global targets set by admin)
        monthly_targets = Target.objects.filter(target_type="monthly")

        for emp in Employee.objects.all():
            # Employee's product-wise sales for that month
            month_sales = (
                Sale.objects.filter(employee=emp, date__year=year, date__month=month)
                .values("product")
                .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
            )
            month_sales_dict = {s["product"]: s for s in month_sales}

            for target in monthly_targets:
                sales_row = month_sales_dict.get(target.product, None)
                achieved_amount = sales_row["total_amount"] if sales_row else 0
                achieved_points = sales_row["total_points"] if sales_row else 0

                # Store in MonthlyTargetHistory
                MonthlyTargetHistory.objects.update_or_create(
                    employee=emp,
                    product=target.product,
                    year=year,
                    month=month,
                    defaults={
                        "target_value": target.target_value,
                        "achieved_value": achieved_amount,
                        "points_value": achieved_points,  # ⚡ new field (see below)
                    },
                )

                self.stdout.write(
                    f"{emp.user.username} | {target.product}: "
                    f"Achieved ₹{achieved_amount}, {achieved_points} pts "
                    f"vs Target ₹{target.target_value}"
                )

        self.stdout.write(self.style.SUCCESS(f"Monthly targets closed for {month}/{year}"))

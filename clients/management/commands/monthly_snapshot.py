# clients/management/commands/monthly_snapshot.py
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone
from clients.models import Sale, MonthlyIncentive, Employee
from decimal import Decimal
import calendar


class Command(BaseCommand):
    help = "Create monthly incentive snapshot for the previous month (or given --year and --month)."

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, help='Year (e.g., 2025)')
        parser.add_argument('--month', type=int, help='Month number (1-12)')

    def handle(self, *args, **options):
        # Determine target year and month (previous month by default)
        now = timezone.now().date()
        y = options.get('year')
        m = options.get('month')

        if y and m:
            year, month = y, m
        else:
            # previous month
            if now.month == 1:
                year = now.year - 1
                month = 12
            else:
                year = now.year
                month = now.month - 1

        # Logging info
        _, last_day = calendar.monthrange(year, month)
        start_date = f"{year}-{month:02d}-01"
        end_date = f"{year}-{month:02d}-{last_day:02d}"
        self.stdout.write(f"Creating incentive snapshots for {year}-{month:02d} ({start_date} to {end_date})")

        # Aggregate sales for that month
        qs = Sale.objects.filter(date__year=year, date__month=month)
        agg = qs.values('employee').annotate(
            total_points=Sum('points'),
            total_amount=Sum('amount'),
        )

        if not agg:
            self.stdout.write("No sales found for this month. Exiting.")
            return

        # Save/update snapshot records
        created_count = 0
        updated_count = 0
        for row in agg:
            emp_id = row['employee']
            total_points = row['total_points'] or Decimal('0.000')
            total_amount = row['total_amount'] or Decimal('0.00')

            try:
                emp = Employee.objects.get(pk=emp_id)
            except Employee.DoesNotExist:
                self.stdout.write(f"Skipping unknown employee id {emp_id}")
                continue

            obj, created = MonthlyIncentive.objects.update_or_create(
                employee=emp,
                year=year,
                month=month,
                defaults={
                    'total_points': total_points,
                    'total_amount': total_amount,
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
            self.stdout.write(f"{'Created' if created else 'Updated'}: {emp.user.username} => {total_points} pts, ₹{total_amount}")

        self.stdout.write(self.style.SUCCESS(
            f"Monthly snapshot completed. Created: {created_count}, Updated: {updated_count}"
        ))

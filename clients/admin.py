from django.contrib import admin
from import_export.admin import ImportExportModelAdmin
from .models import Client, Employee, Sale  # add Sale here
from .models import IncentiveRule, Sale, MonthlyIncentive
from django.urls import path
from django.shortcuts import render
from django.db.models import Sum
from django.http import HttpResponse
import csv
from .models import Sale, MonthlyIncentive, Employee
from datetime import date
from .models import IncentiveRule
from .models import Target
@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')  # show linked User + role
    search_fields = ('user__username', 'user__email')  # search by Django user fields


@admin.register(Client)
class ClientAdmin(ImportExportModelAdmin):
    list_display = (
        'id',
        'name',
        'mapped_to',
        'sip_status',
        'life_status',
        'health_status',
        'motor_status',
        'pms_status',
        'created_at',
    )
    search_fields = ('id', 'name', 'email', 'phone', 'pan')
    list_filter = (
        'sip_status',
        'life_status',
        'health_status',
        'motor_status',
        'pms_status',
        'status',
    )


from django.contrib import admin
from .models import Employee, Target, Sale

@admin.register(Target)
class TargetAdmin(admin.ModelAdmin):
    list_display = ("product", "target_type", "target_value", "created_at")
    list_editable = ("target_value",)
    list_filter = ("target_type", "product")
    search_fields = ("product",)
    fields = ("product", "target_type", "target_value")

    # Prevent duplicate targets
    def has_add_permission(self, request):
        # Allow add only if combination does not exist
        if Target.objects.count() >= (len(Sale.PRODUCT_CHOICES) * 2):
            return False
        return True



# Add these imports at top of clients/admin.py
import csv
from django.http import HttpResponse
from django.db.models import Sum
from decimal import Decimal

# ... your existing imports: admin, ImportExportModelAdmin, Client, Employee, Sale, IncentiveRule, MonthlyIncentive etc.


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "client",
        "employee",
        "product",
        "amount",
        "points",
        "incentive_amount",
        "date",
    )
    search_fields = ("client__name", "employee__user__username", "product")
    list_filter = ("product", "date")
    actions = ("export_aggregated_incentives",)  # register the action

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Admins see all sales
        if request.user.is_superuser or getattr(getattr(request.user, "employee", None), "role", "") == "admin":
            return qs
        # Employees see only their own sales
        emp = getattr(request.user, "employee", None)
        if emp:
            return qs.filter(employee=emp)
        return qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "client":
            # both admin and employees can pick any client (per your rules)
            kwargs["queryset"] = Client.objects.all()
        if db_field.name == "employee" and not request.user.is_superuser:
            emp = getattr(request.user, "employee", None)
            kwargs["initial"] = emp
            kwargs["disabled"] = True
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # If employee creates sale, force set employee to logged-in user
        if not request.user.is_superuser:
            emp = getattr(request.user, "employee", None)
            if emp:
                obj.employee = emp
        # Points and incentive auto-computed in Sale.save()
        super().save_model(request, obj, form, change)

    # -----------------------------
    # Admin action: export aggregated incentives as CSV
    # -----------------------------
    def export_aggregated_incentives(self, request, queryset):
        """
        Aggregates the given Sale queryset by employee and returns a CSV:
        employee_id, username, total_points, total_incentive, total_amount
        Usage: filter by month/date in admin, select all, choose this action.
        """
        # aggregate by employee
        agg = queryset.values('employee').annotate(
            username=Sum('employee__user__username')  # placeholder: will be overridden below
        ).annotate(
            total_points=Sum('points'),
            total_incentive=Sum('incentive_amount'),
            total_amount=Sum('amount'),
        )

        # Build a mapping of employee id -> username (since Sum on username is nonsense)
        # Better approach: fetch employees for IDs found
        emp_ids = [row['employee'] for row in agg]
        employees = {e.id: e.user.username for e in Employee.objects.filter(id__in=emp_ids)}

        # Prepare HTTP response with CSV
        now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"incentive_report_{now_str}.csv"
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow(['employee_id', 'username', 'total_points', 'total_incentive', 'total_amount'])

        # Write rows in a stable order (by total_points desc)
        # Convert Decimal to string to avoid formatting issues
        sorted_rows = sorted(agg, key=lambda r: (r['total_points'] or Decimal('0')), reverse=True)
        for row in sorted_rows:
            emp_id = row['employee']
            username = employees.get(emp_id, "")
            total_points = row.get('total_points') or Decimal('0')
            total_incentive = row.get('total_incentive') or Decimal('0')
            total_amount = row.get('total_amount') or Decimal('0')
            writer.writerow([
                emp_id,
                username,
                f"{total_points:.3f}",
                f"{total_incentive:.2f}",
                f"{total_amount:.2f}",
            ])

        return response

    export_aggregated_incentives.short_description = "Export aggregated incentives (CSV)"



@admin.register(IncentiveRule)
class IncentiveRuleAdmin(admin.ModelAdmin):
    list_display = ('product', 'unit_amount', 'points_per_unit', 'active')
    list_editable = ('unit_amount', 'points_per_unit', 'active')
    search_fields = ('product',)



@admin.register(MonthlyIncentive)
class MonthlyIncentiveAdmin(admin.ModelAdmin):
    list_display = ('employee','year','month','total_points','total_amount','created_at')
    search_fields = ('employee__user__username',)
    list_filter = ('year','month')



def incentive_report_view(request):
    year = int(request.GET.get("year", date.today().year))
    month = int(request.GET.get("month", date.today().month))

    # Use snapshot if exists, else compute live
    qs = MonthlyIncentive.objects.filter(year=year, month=month)
    if not qs.exists():
        sales = Sale.objects.filter(date__year=year, date__month=month)
        qs = sales.values("employee__id", "employee__user__username").annotate(
            total_points=Sum("points"), total_amount=Sum("amount")
        )

    # Handle CSV export
    if "export" in request.GET:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="incentives_{year}_{month}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Employee", "Total Points", "Total Amount"])
        for row in qs:
            if isinstance(row, dict):
                writer.writerow([row["employee__user__username"], row["total_points"], row["total_amount"]])
            else:
                writer.writerow([row.employee.user.username, row.total_points, row.total_amount])
        return response

    context = {
        **admin.site.each_context(request),
        "title": f"Incentive Report ({month}/{year})",
        "qs": qs,
        "year": year,
        "month": month,
    }
    return render(request, "admin/incentive_report.html", context)


# ✅ Hook the view into Admin URLs (no register_view!)
def get_admin_urls(urls):
    def _get_urls():
        my_urls = [
            path("incentive-report/", admin.site.admin_view(incentive_report_view), name="incentive-report"),
        ]
        return my_urls + urls
    return _get_urls

admin.site.get_urls = get_admin_urls(admin.site.get_urls())










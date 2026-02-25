"""Dashboard views: admin, employee, management, performance, net business/SIP."""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from calendar import monthrange, month_name
from itertools import cycle

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.utils.timezone import now
from django.db.models import Sum, Q, Count
from django.db.models.functions import TruncDay, TruncMonth, TruncYear
from django.core.exceptions import FieldError
from django.db import transaction

from ..models import (
    Client,
    Sale,
    Employee,
    Target,
    MonthlyTargetHistory,
    CalendarEvent,
    CallRecord,
    NetBusinessEntry,
    NetSipEntry,
    Notification,
    LeadFollowUp,
    ManagerAccessConfig,
)
from ..forms import (
    EmployeeCreateForm,
    EmployeeDeactivateForm,
)
from .helpers import get_manager_access


@login_required
def admin_dashboard(request):
    emp = getattr(request.user, "employee", None)
    if not (request.user.is_superuser or (emp and emp.role in ("admin", "manager"))):
        return redirect("clients:employee_dashboard")
    today = timezone.now().date()
    month = today.month
    year = today.year

    admin_emp = getattr(request.user, "employee", None)

    products = [p for p, _ in Sale.PRODUCT_CHOICES]
    product_labels = {
        "SIP": "SIP",
        "Lumsum": "Lumpsum",
        "Life Insurance": "Life Insurance",
        "Health Insurance": "Health Insurance",
        "Motor Insurance": "Motor Insurance",
        "PMS": "PMS",
        "COB": "COB",
    }

    def _build_breakup(data_map):
        return [
            {
                "product": product,
                "label": product_labels.get(product, product),
                "value": data_map.get(product, Decimal("0")),
            }
            for product in products
        ]

    now_ts = timezone.now()
    today_date = now_ts.date()
    week_start = today_date - timedelta(days=today_date.weekday())
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    followup_qs = (
        LeadFollowUp.objects.filter(status="pending")
        .filter(
            Q(scheduled_time__lt=now_ts) |
            Q(scheduled_time__date__gte=week_start, scheduled_time__date__lte=week_start + timedelta(days=6))
        )
        .select_related("lead", "assigned_to__user")
        .order_by("scheduled_time")
    )
    followups_by_date = {}
    overdue_followups = []
    for f in followup_qs:
        f.is_overdue = f.scheduled_time < now_ts
        if f.is_overdue:
            overdue_followups.append(f)
        fdate = f.scheduled_time.date()
        followups_by_date.setdefault(fdate, []).append(f)
    upcoming_followups = list(followup_qs)
    all_employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username")

    all_sales_qs = Sale.objects.all()
    monthly_sales_qs = Sale.objects.filter(status=Sale.STATUS_APPROVED, created_at__year=year, created_at__month=month)
    approved_sales_all = Sale.objects.filter(status=Sale.STATUS_APPROVED)

    total_clients = Client.objects.count()
    total_sales = monthly_sales_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_points = monthly_sales_qs.aggregate(total=Sum("points"))["total"] or Decimal("0")
    total_salary_all = Employee.objects.aggregate(total=Sum("salary"))["total"] or Decimal("0")
    admin_points_scale = max(total_points, total_salary_all, Decimal("1"))
    admin_salary_ratio = (total_salary_all / admin_points_scale) * Decimal("100") if admin_points_scale else Decimal("0")
    admin_points_ratio = (total_points / admin_points_scale) * Decimal("100") if admin_points_scale else Decimal("0")
    admin_extra_points = max(total_points - total_salary_all, Decimal("0"))
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])

    admin_self_sales = Decimal("0")
    admin_self_points = Decimal("0")
    admin_self_pending_points = Decimal("0")
    admin_self_points_map = {p: Decimal("0") for p in products}
    admin_self_sales_map = {p: Decimal("0") for p in products}
    if admin_emp:
        self_sales_qs = Sale.objects.filter(employee=admin_emp, status=Sale.STATUS_APPROVED, date__year=year, date__month=month)
        admin_self_sales = self_sales_qs.aggregate(total=Sum("amount"))['total'] or Decimal("0")
        admin_self_points = self_sales_qs.aggregate(total=Sum("points"))['total'] or Decimal("0")
        admin_self_pending_points = Sale.objects.filter(employee=admin_emp, status=Sale.STATUS_PENDING, date__year=year, date__month=month).aggregate(total=Sum("points"))['total'] or Decimal("0")

        for entry in self_sales_qs.values("product").annotate(total=Sum("points")):
            admin_self_points_map[entry["product"]] = entry["total"] or Decimal("0")
        for entry in self_sales_qs.values("product").annotate(total=Sum("amount")):
            admin_self_sales_map[entry["product"]] = entry["total"] or Decimal("0")

    sip_sales = monthly_sales_qs.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = monthly_sales_qs.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = monthly_sales_qs.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = monthly_sales_qs.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = monthly_sales_qs.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = monthly_sales_qs.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    overall_points_map = {p: Decimal("0") for p in products}
    overall_sales_map = {p: Decimal("0") for p in products}
    for entry in monthly_sales_qs.values("product").annotate(total=Sum("points")):
        overall_points_map[entry["product"]] = entry["total"] or Decimal("0")
    for entry in monthly_sales_qs.values("product").annotate(total=Sum("amount")):
        overall_sales_map[entry["product"]] = entry["total"] or Decimal("0")

    admin_self_points_breakup = _build_breakup(admin_self_points_map)
    admin_overall_points_breakup = _build_breakup(overall_points_map)
    admin_self_sales_breakup = _build_breakup(admin_self_sales_map)
    admin_overall_sales_breakup = _build_breakup(overall_sales_map)

    daily_targets = Target.objects.filter(target_type="daily")
    monthly_targets = Target.objects.filter(target_type="monthly")
    daily_target_map = {t.product: t.target_value for t in daily_targets}
    monthly_target_map = {t.product: t.target_value for t in monthly_targets}
    active_employee_count = Employee.objects.filter(role="employee", active=True).count()

    admin_daily_targets_display = []
    admin_monthly_targets_display = []
    if admin_emp:
        admin_today_sales = monthly_sales_qs.filter(employee=admin_emp, date=today).values("product").annotate(total=Sum("amount"))
        admin_today_map = {s["product"]: s["total"] for s in admin_today_sales}
        admin_month_sales = monthly_sales_qs.filter(employee=admin_emp).values("product").annotate(total=Sum("amount"))
        admin_month_map = {s["product"]: s["total"] for s in admin_month_sales}

        for product in products:
            target_val = daily_target_map.get(product, Decimal("0"))
            achieved = admin_today_map.get(product, Decimal("0"))
            progress = (achieved / target_val * 100) if target_val else 0
            admin_daily_targets_display.append({
                "product": product,
                "target_value": target_val,
                "achieved": achieved,
                "progress": progress,
            })

        for product in products:
            target_val = monthly_target_map.get(product, Decimal("0"))
            achieved = admin_month_map.get(product, Decimal("0"))
            progress = (achieved / target_val * 100) if target_val else 0
            admin_monthly_targets_display.append({
                "product": product,
                "target_value": target_val,
                "achieved": achieved,
                "progress": progress,
            })

    overall_daily_progress = []
    for product in products:
        target_value = daily_target_map.get(product, Decimal("0")) * (active_employee_count or 0)
        achieved = approved_sales_all.filter(product=product, date=today).aggregate(total=Sum("amount"))['total'] or 0
        progress = (achieved / target_value * 100) if target_value else 0
        overall_daily_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

    overall_monthly_progress = []
    for product in products:
        achieved = monthly_sales_qs.filter(product=product).aggregate(total=Sum("amount"))['total'] or 0
        target_base = monthly_target_map.get(product, Decimal("0"))
        target_value = (target_base or 0) * (active_employee_count or 0)
        progress = (achieved / target_value * 100) if target_value else 0
        overall_monthly_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

    employees = Employee.objects.select_related("user").filter(active=True)

    daily_employee_product = []
    for emp_obj in employees:
        emp_entry = {
            "employee": emp_obj.user.username if hasattr(emp_obj, "user") else emp_obj.name,
            "products": []
        }
        for product in products:
            achieved = approved_sales_all.filter(employee=emp_obj, product=product, date=today).aggregate(total=Sum("amount"))['total'] or 0
            target = daily_target_map.get(product, 0)
            progress = (achieved / target * 100) if target else 0
            emp_entry["products"].append({
                "product": product,
                "achieved": achieved,
                "target": target,
                "progress": progress,
            })
        daily_employee_product.append(emp_entry)

    monthly_employee_product = []
    for emp_obj in employees:
        emp_entry = {
            "employee": emp_obj.user.username if hasattr(emp_obj, "user") else emp_obj.name,
            "products": []
        }
        for product in products:
            achieved = approved_sales_all.filter(
                employee=emp_obj,
                product=product,
                date__year=year,
                date__month=month
            ).aggregate(total=Sum("amount"))['total'] or 0
            target = monthly_target_map.get(product, 0)
            progress = (achieved / target * 100) if target else 0
            emp_entry["products"].append({
                "product": product,
                "achieved": achieved,
                "target": target,
                "progress": progress,
            })
        monthly_employee_product.append(emp_entry)

    monthly_summary = {
        "total_clients": Client.objects.filter(created_at__year=year, created_at__month=month).count(),
        "total_sales": total_sales,
        "total_points": total_points,
        "sip": sip_sales,
        "lumpsum": lumsum_sales,
        "life": life_sales,
        "health": health_sales,
        "motor": motor_sales,
        "pms": pms_sales,
    }

    notifications = []
    unread_notifications = 0
    if request.user.is_authenticated:
        notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:10]
        unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False).count()

    context = {
        "total_clients": total_clients,
        "total_sales": total_sales,
        "total_points": total_points,
        "total_salary_all": total_salary_all,
        "admin_salary_ratio": admin_salary_ratio,
        "admin_points_ratio": admin_points_ratio,
        "admin_extra_points": admin_extra_points,
        "admin_self_sales": admin_self_sales,
        "admin_self_points": admin_self_points,
        "admin_self_pending_points": admin_self_pending_points,
        "admin_self_points_breakup": admin_self_points_breakup,
        "admin_overall_points_breakup": admin_overall_points_breakup,
        "admin_self_sales_breakup": admin_self_sales_breakup,
        "admin_overall_sales_breakup": admin_overall_sales_breakup,
        "admin_daily_targets": admin_daily_targets_display,
        "admin_monthly_targets": admin_monthly_targets_display,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "overall_daily_progress": overall_daily_progress,
        "overall_monthly_progress": overall_monthly_progress,
        "daily_employee_product": daily_employee_product,
        "monthly_employee_product": monthly_employee_product,
        "monthly_summary": monthly_summary,
        "notifications": notifications,
        "unread_notifications": unread_notifications,
        "upcoming_followups": upcoming_followups,
        "followups_by_date": followups_by_date,
        "overdue_followups": overdue_followups,
        "week_dates": week_dates,
        "today_date": today_date,
        "is_admin_dashboard": True,
        "all_employees": all_employees,
        "month_start": month_start,
        "month_end": month_end,
    }

    return render(request, "dashboards/admin_dashboard.html", context)


@login_required
def employee_management(request):
    admin_emp = getattr(request.user, "employee", None)
    if not admin_emp or admin_emp.role != "admin":
        return HttpResponseForbidden("Admins only.")

    create_form = EmployeeCreateForm()
    manager_access = ManagerAccessConfig.current()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            create_form = EmployeeCreateForm(request.POST)
            if create_form.is_valid():
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=create_form.cleaned_data["username"],
                        email=create_form.cleaned_data.get("email"),
                        password=create_form.cleaned_data["password"],
                        is_active=True,
                    )
                    Employee.objects.create(
                        user=user,
                        role=create_form.cleaned_data["role"],
                        salary=create_form.cleaned_data["salary"],
                        active=True,
                    )
                messages.success(request, "Employee created successfully.")
                return redirect("clients:employee_management")
            else:
                error_text = "; ".join([
                    "; ".join([str(msg) for msg in errs]) for errs in create_form.errors.values()
                ])
                messages.error(request, error_text or "Please correct the errors in the form.")
        elif action == "update_manager_access":
            bool_fields = [
                "allow_view_all_sales",
                "allow_approve_sales",
                "allow_edit_sales",
                "allow_manage_incentives",
                "allow_recalc_points",
                "allow_client_analysis",
                "allow_employee_performance",
                "allow_lead_management",
                "allow_calling_admin",
                "allow_business_tracking",
            ]
            for field in bool_fields:
                setattr(manager_access, field, field in request.POST)
            manager_access.save(update_fields=bool_fields + ["updated_at"])
            messages.success(request, "Manager rights updated.")
            return redirect("clients:employee_management")
        elif action in ("deactivate", "activate"):
            status_form = EmployeeDeactivateForm(request.POST)
            if status_form.is_valid():
                emp_obj = get_object_or_404(Employee, pk=status_form.cleaned_data["employee_id"])
                if action == "deactivate":
                    if not emp_obj.active:
                        messages.info(request, "Employee is already inactive.")
                        return redirect("clients:employee_management")

                    other_emps = list(Employee.objects.filter(active=True).exclude(id=emp_obj.id))
                    mapped_clients = list(Client.objects.filter(mapped_to=emp_obj))

                    if mapped_clients and not other_emps:
                        messages.error(request, "Cannot deactivate the last active employee while they have mapped clients. Reassign or add another employee first.")
                        return redirect("clients:employee_management")

                    if other_emps:
                        rr = cycle(other_emps)
                        for client in mapped_clients:
                            new_emp = next(rr)
                            client.reassign_to(new_emp, changed_by=request.user, note="Auto-reassigned on deactivation")

                    with transaction.atomic():
                        emp_obj.active = False
                        emp_obj.save(update_fields=["active"])
                        if emp_obj.user_id:
                            emp_obj.user.is_active = False
                            emp_obj.user.save(update_fields=["is_active"])

                    messages.success(request, f"Deactivated {emp_obj.user.username} and reassigned {len(mapped_clients)} clients evenly.")
                    return redirect("clients:employee_management")

                if emp_obj.active:
                    messages.info(request, "Employee is already active.")
                    return redirect("clients:employee_management")

                with transaction.atomic():
                    emp_obj.active = True
                    emp_obj.save(update_fields=["active"])
                    if emp_obj.user_id:
                        emp_obj.user.is_active = True
                        emp_obj.user.save(update_fields=["is_active"])

                messages.success(request, f"Reactivated {emp_obj.user.username}.")
                return redirect("clients:employee_management")
            else:
                messages.error(request, "Invalid employee action request.")

    employees = Employee.objects.select_related("user").all().order_by("-active", "user__username")
    context = {
        "employees": employees,
        "create_form": create_form,
        "manager_access": manager_access,
    }
    return render(request, "employees/manage.html", context)


@login_required
def employee_dashboard(request):
    emp = request.user.employee
    today = now().date()
    role = getattr(emp, "role", "")
    is_manager = role == "manager"
    is_admin = role == "admin"
    manager_access = get_manager_access() if is_manager else None
    allow_company_sections = is_admin or (is_manager and manager_access and manager_access.allow_employee_performance)
    month_start = date(today.year, today.month, 1)
    month_end = date(today.year, today.month, monthrange(today.year, today.month)[1])

    now_ts = timezone.now()
    today_date = now_ts.date()
    week_start = today_date - timedelta(days=today_date.weekday())
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    followup_qs = (
        LeadFollowUp.objects.filter(status="pending", assigned_to=emp)
        .filter(
            Q(scheduled_time__lt=now_ts) |
            Q(scheduled_time__date__gte=week_start, scheduled_time__date__lte=week_start + timedelta(days=6))
        )
        .select_related("lead")
        .order_by("scheduled_time")
    )
    followups_by_date = {}
    overdue_followups = []
    for f in followup_qs:
        f.is_overdue = f.scheduled_time < now_ts
        if f.is_overdue:
            overdue_followups.append(f)
        fdate = f.scheduled_time.date()
        followups_by_date.setdefault(fdate, []).append(f)
    upcoming_followups = list(followup_qs)

    products = [p for p, _ in Sale.PRODUCT_CHOICES]

    monthly_sales_approved = Sale.objects.filter(
        employee=emp,
        status=Sale.STATUS_APPROVED,
        date__year=today.year,
        date__month=today.month
    )
    monthly_sales_pending = Sale.objects.filter(
        employee=emp,
        status=Sale.STATUS_PENDING,
        date__year=today.year,
        date__month=today.month
    )
    today_sales_qs = monthly_sales_approved.filter(date=today)

    total_sales = monthly_sales_approved.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    total_points = monthly_sales_approved.aggregate(total=Sum("points"))["total"] or Decimal("0")
    pending_points = monthly_sales_pending.aggregate(total=Sum("points"))["total"] or Decimal("0")
    salary_points = getattr(emp, "salary", Decimal("0")) or Decimal("0")
    if not isinstance(salary_points, Decimal):
        salary_points = Decimal(str(salary_points))
    points_scale = max(total_points, salary_points, Decimal("1"))
    salary_ratio = (salary_points / points_scale) * Decimal("100") if points_scale else Decimal("0")
    points_ratio = (total_points / points_scale) * Decimal("100") if points_scale else Decimal("0")
    extra_points = max(total_points - salary_points, Decimal("0"))

    sip_sales = monthly_sales_approved.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = monthly_sales_approved.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = monthly_sales_approved.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = monthly_sales_approved.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = monthly_sales_approved.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = monthly_sales_approved.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    product_points_map = {p: Decimal("0") for p in products}
    product_points_qs = monthly_sales_approved.values("product").annotate(total=Sum("points"))
    for entry in product_points_qs:
        product_points_map[entry["product"]] = entry["total"] or Decimal("0")

    product_labels = {
        "SIP": "SIP",
        "Lumsum": "Lumpsum",
        "Life Insurance": "Life Insurance",
        "Health Insurance": "Health Insurance",
        "Motor Insurance": "Motor Insurance",
        "PMS": "PMS",
        "COB": "COB",
    }
    product_point_breakup = [
        {
            "product": product,
            "label": product_labels.get(product, product),
            "points": product_points_map.get(product, Decimal("0")),
        }
        for product in products
    ]

    today_sales = today_sales_qs.values("product").annotate(total=Sum("amount"))
    today_sales_dict = {s["product"]: s["total"] for s in today_sales}
    todays_tasks = CalendarEvent.objects.filter(
        employee=request.user.employee,
        scheduled_time__date=today,
    ).order_by("scheduled_time")

    month_sales = monthly_sales_approved.values("product").annotate(total=Sum("amount"))
    month_sales_dict = {s["product"]: s["total"] for s in month_sales}
    product_sales_breakup = [
        {
            "product": product,
            "label": product_labels.get(product, product),
            "amount": month_sales_dict.get(product, Decimal("0")),
        }
        for product in products
    ]

    daily_targets = Target.objects.filter(target_type="daily")
    monthly_targets = Target.objects.filter(target_type="monthly")
    daily_target_map = {t.product: t.target_value for t in daily_targets}
    monthly_target_map = {t.product: t.target_value for t in monthly_targets}

    daily_targets_display = []
    for product in products:
        target_value = daily_target_map.get(product, Decimal("0"))
        achieved = today_sales_dict.get(product, Decimal("0"))
        progress = (achieved / target_value * 100) if target_value else 0
        daily_targets_display.append({
            "product": product,
            "target_value": target_value,
            "achieved": achieved,
            "progress": progress,
        })

    monthly_targets_display = []
    for product in products:
        target_value = monthly_target_map.get(product, Decimal("0"))
        achieved = month_sales_dict.get(product, Decimal("0"))
        progress = (achieved / target_value * 100) if target_value else 0
        monthly_targets_display.append({
            "product": product,
            "target_value": target_value,
            "achieved": achieved,
            "progress": progress,
        })

    history = MonthlyTargetHistory.objects.filter(employee=emp).order_by("-year", "-month")[:6]

    todays_events = CalendarEvent.objects.filter(
        employee=request.user.employee,
        scheduled_time__date=today,
        status="pending",
    ).order_by("scheduled_time")

    # Company-wide aggregates for managers/admins
    overall_daily_progress = []
    overall_monthly_progress = []
    daily_employee_product = []
    monthly_employee_product = []
    overall_product_point_breakup = []
    overall_product_sales_breakup = []

    if allow_company_sections:
        approved_sales_all = Sale.objects.filter(status=Sale.STATUS_APPROVED)
        monthly_sales_qs = approved_sales_all.filter(date__year=today.year, date__month=today.month)
        active_employee_count = Employee.objects.filter(role="employee", active=True).count()

        for product in products:
            target_value = daily_target_map.get(product, Decimal("0")) * (active_employee_count or 0)
            achieved = approved_sales_all.filter(product=product, date=today).aggregate(total=Sum("amount"))['total'] or 0
            progress = (achieved / target_value * 100) if target_value else 0
            overall_daily_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

        for product in products:
            achieved = monthly_sales_qs.filter(product=product).aggregate(total=Sum("amount"))['total'] or 0
            target_base = monthly_target_map.get(product, Decimal("0"))
            target_value = (target_base or 0) * (active_employee_count or 0)
            progress = (achieved / target_value * 100) if target_value else 0
            overall_monthly_progress.append({"product": product, "achieved": achieved, "target": target_value, "progress": progress})

        employees_all = Employee.objects.select_related("user").filter(active=True)
        for e in employees_all:
            emp_entry_daily = {
                "employee": e.user.username if hasattr(e, "user") else getattr(e, "name", ""),
                "products": [],
            }
            for product in products:
                achieved = approved_sales_all.filter(employee=e, product=product, date=today).aggregate(total=Sum("amount"))['total'] or 0
                target = daily_target_map.get(product, 0)
                progress = (achieved / target * 100) if target else 0
                emp_entry_daily["products"].append({
                    "product": product,
                    "achieved": achieved,
                    "target": target,
                    "progress": progress,
                })
            daily_employee_product.append(emp_entry_daily)

        for e in employees_all:
            emp_entry_monthly = {
                "employee": e.user.username if hasattr(e, "user") else getattr(e, "name", ""),
                "products": [],
            }
            for product in products:
                achieved = approved_sales_all.filter(
                    employee=e,
                    product=product,
                    date__year=today.year,
                    date__month=today.month,
                ).aggregate(total=Sum("amount"))['total'] or 0
                target = monthly_target_map.get(product, 0)
                progress = (achieved / target * 100) if target else 0
                emp_entry_monthly["products"].append({
                    "product": product,
                    "achieved": achieved,
                    "target": target,
                    "progress": progress,
                })
            monthly_employee_product.append(emp_entry_monthly)

        overall_points_map = {p: Decimal("0") for p in products}
        overall_sales_map = {p: Decimal("0") for p in products}
        for entry in monthly_sales_qs.values("product").annotate(total=Sum("points")):
            overall_points_map[entry["product"]] = entry["total"] or Decimal("0")
        for entry in monthly_sales_qs.values("product").annotate(total=Sum("amount")):
            overall_sales_map[entry["product"]] = entry["total"] or Decimal("0")

        overall_product_point_breakup = [
            {
                "product": product,
                "label": product_labels.get(product, product),
                "points": overall_points_map.get(product, Decimal("0")),
            }
            for product in products
        ]

        overall_product_sales_breakup = [
            {
                "product": product,
                "label": product_labels.get(product, product),
                "amount": overall_sales_map.get(product, Decimal("0")),
            }
            for product in products
        ]

    context = {
        "total_sales": total_sales,
        "total_points": total_points,
        "salary_points": salary_points,
        "salary_ratio": salary_ratio,
        "points_ratio": points_ratio,
        "extra_points": extra_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "todays_events": todays_events,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "today_sales_dict": today_sales_dict,
        "month_sales_dict": month_sales_dict,
        "daily_targets": daily_targets_display,
        "monthly_targets": monthly_targets_display,
        "history": history,
        "upcoming_followups": upcoming_followups,
        "followups_by_date": followups_by_date,
        "overdue_followups": overdue_followups,
        "week_dates": week_dates,
        "today_date": today_date,
        "is_admin_dashboard": False,
        "pending_points": pending_points,
        "product_point_breakup": product_point_breakup,
        "product_sales_breakup": product_sales_breakup,
        "overall_product_point_breakup": overall_product_point_breakup,
        "overall_product_sales_breakup": overall_product_sales_breakup,
        "show_company_sections": allow_company_sections,
        "overall_daily_progress": overall_daily_progress,
        "overall_monthly_progress": overall_monthly_progress,
        "monthly_employee_product": monthly_employee_product,
        "is_manager": is_manager,
        "is_admin": is_admin,
        "month_start": month_start,
        "month_end": month_end,
    }
    return render(request, "dashboards/employee_dashboard.html", context)


@login_required
def employee_performance(request):
    """Employee performance overview (MVP)."""
    emp_id = request.GET.get('employee_id')
    is_manager = False
    if hasattr(request.user, 'employee') and request.user.employee.role in ('admin', 'manager'):
        is_manager = True
    if request.user.is_superuser:
        is_manager = True

    if emp_id and is_manager:
        employee = get_object_or_404(Employee, id=emp_id)
    elif hasattr(request.user, 'employee'):
        employee = request.user.employee
    else:
        messages.error(request, 'No employee selected and you are not mapped to an employee.')
        return redirect('clients:admin_dashboard')

    try:
        start_str = request.GET.get('start')
        end_str = request.GET.get('end')
        if start_str:
            start = datetime.fromisoformat(start_str).date()
        else:
            start = date.today() - timedelta(days=30)
        if end_str:
            end = datetime.fromisoformat(end_str).date()
        else:
            end = date.today()
    except Exception:
        start = date.today() - timedelta(days=30)
        end = date.today()

    sales_qs = Sale.objects.filter(employee=employee, date__range=(start, end))
    total_sales = sales_qs.count()
    try:
        total_amount = sales_qs.aggregate(total=Sum('amount'))['total'] or 0
    except FieldError:
        try:
            total_amount = sales_qs.aggregate(total=Sum('total_amount'))['total'] or 0
        except FieldError:
            total_amount = 0
    points = sales_qs.aggregate(total=Sum('points'))['total'] or 0

    calls_qs = CallRecord.objects.filter(employee=employee, call_time__date__range=(start, end))
    calls_made = calls_qs.count()
    connects = calls_qs.filter(status__in=['connected', 'success']).count() if calls_made else 0
    connect_rate = (connects / calls_made * 100) if calls_made else 0
    conversion_rate = (total_sales / calls_made * 100) if calls_made else 0

    days = []
    sales_series = []
    calls_series = []
    current = start
    while current <= end:
        days.append(current.strftime('%Y-%m-%d'))
        sales_series.append(sales_qs.filter(date=current).aggregate(cnt=Count('id'))['cnt'] or 0)
        calls_series.append(calls_qs.filter(call_time__date=current).aggregate(cnt=Count('id'))['cnt'] or 0)
        current += timedelta(days=1)

    recent_sales = sales_qs.order_by('-date')[:10]

    if request.GET.get('export') == 'csv':
        import csv as _csv

        resp = HttpResponse(content_type='text/csv')
        filename = f"employee_{employee.id}_performance_{start}_{end}.csv"
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = _csv.writer(resp)
        writer.writerow(['date', 'client', 'amount', 'points', 'product'])
        for s in sales_qs.order_by('date'):
            client_name = s.client.name if getattr(s, 'client', None) else ''
            amount = getattr(s, 'total_amount', None) or getattr(s, 'amount', None) or ''
            points_v = getattr(s, 'points', '')
            product = getattr(s, 'product', '')
            writer.writerow([s.date, client_name, amount, points_v, product])
        return resp

    context = {
        'employee': employee,
        'start': start,
        'end': end,
        'total_sales': total_sales,
        'total_amount': total_amount,
        'points': points,
        'calls_made': calls_made,
        'connects': connects,
        'connect_rate': round(connect_rate, 1),
        'conversion_rate': round(conversion_rate, 1),
        'days': days,
        'sales_series': sales_series,
        'calls_series': calls_series,
        'recent_sales': recent_sales,
        'is_manager': is_manager,
    }

    if is_manager:
        context['employees'] = Employee.objects.select_related('user').all()

    return render(request, 'sales/employee_performance.html', context)


@login_required
def net_business(request):
    """Net business dashboard: shows sales minus redemptions/SIP stoppage."""
    if not (request.user.is_superuser or (hasattr(request.user, 'employee') and request.user.employee.role in ('admin', 'manager'))):
        messages.error(request, 'You do not have permission to view Net Business.')
        return redirect('clients:admin_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action', 'add')
        try:
            if action == 'bulk_delete':
                selected_ids = request.POST.getlist('selected_ids')
                if not selected_ids:
                    raise ValueError('Select at least one entry to delete')
                deleted_count, _ = NetBusinessEntry.objects.filter(id__in=selected_ids).delete()
                messages.success(request, f'Deleted {deleted_count} entries')
                return redirect('clients:net_business')

            if action == 'delete':
                entry_id = request.POST.get('entry_id')
                if not entry_id:
                    raise ValueError('Missing entry id')
                NetBusinessEntry.objects.filter(id=entry_id).delete()
                messages.success(request, 'Entry deleted')
                return redirect('clients:net_business')

            entry_type = request.POST.get('entry_type')
            amount = float(request.POST.get('amount'))
            month_val = int(request.POST.get('month'))
            year_val = int(request.POST.get('year'))
            note = request.POST.get('note', '')

            if entry_type not in ('sale', 'redemption'):
                raise ValueError('Choose Sale or Redemption')
            if month_val < 1 or month_val > 12:
                raise ValueError('Month must be 1-12')

            entry_date = date(year_val, month_val, 1)

            if action == 'update':
                entry_id = request.POST.get('entry_id')
                if not entry_id:
                    raise ValueError('Missing entry id')
                entry = NetBusinessEntry.objects.get(id=entry_id)
                entry.entry_type = entry_type
                entry.amount = amount
                entry.date = entry_date
                entry.note = note
                entry.save(update_fields=['entry_type', 'amount', 'date', 'note'])
                messages.success(request, 'Entry updated')
            else:
                NetBusinessEntry.objects.create(
                    entry_type=entry_type,
                    amount=amount,
                    date=entry_date,
                    note=note,
                    created_by=request.user,
                )
                messages.success(request, 'Entry added')
            return redirect('clients:net_business')
        except Exception as e:
            messages.error(request, f'Invalid input: {e}')

    try:
        year_picker_raw = request.GET.get('year_picker')
        selected_year = int(year_picker_raw) if year_picker_raw else date.today().year
    except Exception:
        selected_year = date.today().year

    gran = request.GET.get('granularity', 'month')
    series_mode = request.GET.get('series_mode', 'net')

    current_year = date.today().year
    entry_years = list(NetBusinessEntry.objects.values_list('date__year', flat=True).distinct())
    year_options = sorted(set(list(range(current_year + 1, current_year - 5, -1)) + entry_years), reverse=True)

    if gran == 'year':
        max_year = max(entry_years + [current_year]) if entry_years else current_year
        min_year = max_year - 4
        start = date(min_year, 1, 1)
        end = date(max_year, 12, 31)
    else:
        start = date(selected_year, 1, 1)
        end = date(selected_year, 12, 31)

    try:
        table_year = int(request.GET.get('table_year', selected_year))
    except Exception:
        table_year = selected_year

    entries_qs = NetBusinessEntry.objects.filter(date__range=(start, end))

    if gran == 'day':
        entries_grouped = entries_qs.annotate(period=TruncDay('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    elif gran == 'year':
        entries_grouped = entries_qs.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    else:
        entries_grouped = entries_qs.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')

    data = {}
    for r in entries_grouped:
        key = r['period'].date() if hasattr(r['period'], 'date') else r['period']
        if key not in data:
            data[key] = {'sales': 0, 'redemptions': 0}
        if r['entry_type'] == 'sale':
            data[key]['sales'] = float(r['total'] or 0)
        else:
            data[key]['redemptions'] = float(r['total'] or 0)

    period_totals = []
    if gran == 'year':
        min_year = start.year
        max_year = end.year
        for yr in range(min_year, max_year + 1):
            key = date(yr, 1, 1)
            sales_total = data.get(key, {}).get('sales', 0)
            red_total = data.get(key, {}).get('redemptions', 0)
            net_total = sales_total - red_total
            period_totals.append({
                'period': key.isoformat(),
                'label': str(yr),
                'sales': round(sales_total, 2),
                'redemptions': round(red_total, 2),
                'net': round(net_total, 2),
            })
    else:
        periods = [date(start.year, m, 1) for m in range(1, 13)]
        for p in periods:
            sales_total = data.get(p, {}).get('sales', 0)
            red_total = data.get(p, {}).get('redemptions', 0)
            net_total = sales_total - red_total
            label = p.strftime('%b %Y') if gran == 'month' else p.strftime('%d %b %Y')
            period_totals.append({
                'period': p.isoformat(),
                'label': label,
                'sales': round(sales_total, 2),
                'redemptions': round(red_total, 2),
                'net': round(net_total, 2),
            })

    if gran == 'year':
        table_entries = NetBusinessEntry.objects.filter(date__range=(start, end))
        table_grouped = table_entries.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(y, 1, 1): {'sales': 0, 'redemptions': 0} for y in range(start.year, end.year + 1)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'sales': 0, 'redemptions': 0}
            if r['entry_type'] == 'sale':
                table_map[k]['sales'] += float(r['total'] or 0)
            else:
                table_map[k]['redemptions'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['sales'] - val['redemptions']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'sales': val['sales'], 'redemptions': val['redemptions'], 'net': net_val})
    else:
        table_entries = NetBusinessEntry.objects.filter(date__year=table_year)
        table_grouped = table_entries.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(table_year, m, 1): {'sales': 0, 'redemptions': 0} for m in range(1, 13)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'sales': 0, 'redemptions': 0}
            if r['entry_type'] == 'sale':
                table_map[k]['sales'] += float(r['total'] or 0)
            else:
                table_map[k]['redemptions'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%b %Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['sales'] - val['redemptions']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'sales': val['sales'], 'redemptions': val['redemptions'], 'net': net_val})

    entry_list = NetBusinessEntry.objects.order_by('-date', '-created_at')[:200]

    start_str = start.isoformat() if hasattr(start, 'isoformat') else str(start)
    end_str = end.isoformat() if hasattr(end, 'isoformat') else str(end)

    table_len = len(table_rows)
    sales_sum = sum(r.get('sales', 0) for r in table_rows)
    reds_sum = sum(r.get('redemptions', 0) for r in table_rows)
    net_sum = sum(r.get('net', 0) for r in table_rows)

    def _avg(total):
        return total / table_len if table_len else 0

    context = {
        'start': start,
        'end': end,
        'start_str': start_str,
        'end_str': end_str,
        'granularity': gran,
        'series_mode': series_mode,
        'period_totals_json': json.dumps(period_totals),
        'month_table': table_rows,
        'table_year': table_year,
        'year_options': year_options,
        'entry_list': entry_list,
        'table_totals': {
            'sales_sum': sales_sum,
            'reds_sum': reds_sum,
            'net_sum': net_sum,
            'sales_avg': _avg(sales_sum),
            'reds_avg': _avg(reds_sum),
            'net_avg': _avg(net_sum),
        },
    }

    return render(request, 'dashboards/net_business.html', context)


@login_required
def net_sip(request):
    """Net SIP dashboard: SIP fresh minus SIP stopped."""
    if not (request.user.is_superuser or (hasattr(request.user, 'employee') and request.user.employee.role in ('admin', 'manager'))):
        messages.error(request, 'You do not have permission to view Net SIP.')
        return redirect('clients:admin_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action', 'add')
        try:
            if action == 'bulk_delete':
                selected_ids = request.POST.getlist('selected_ids')
                if not selected_ids:
                    raise ValueError('Select at least one entry to delete')
                deleted_count, _ = NetSipEntry.objects.filter(id__in=selected_ids).delete()
                messages.success(request, f'Deleted {deleted_count} entries')
                return redirect('clients:net_sip')

            if action == 'delete':
                entry_id = request.POST.get('entry_id')
                if not entry_id:
                    raise ValueError('Missing entry id')
                NetSipEntry.objects.filter(id=entry_id).delete()
                messages.success(request, 'Entry deleted')
                return redirect('clients:net_sip')

            entry_type = request.POST.get('entry_type')
            amount = float(request.POST.get('amount'))
            month_val = int(request.POST.get('month'))
            year_val = int(request.POST.get('year'))
            note = request.POST.get('note', '')

            if entry_type not in ('fresh', 'stopped'):
                raise ValueError('Choose SIP Fresh or SIP Stopped')
            if month_val < 1 or month_val > 12:
                raise ValueError('Month must be 1-12')

            entry_date = date(year_val, month_val, 1)

            if action == 'update':
                entry_id = request.POST.get('entry_id')
                if not entry_id:
                    raise ValueError('Missing entry id')
                entry = NetSipEntry.objects.get(id=entry_id)
                entry.entry_type = entry_type
                entry.amount = amount
                entry.date = entry_date
                entry.note = note
                entry.save(update_fields=['entry_type', 'amount', 'date', 'note'])
                messages.success(request, 'Entry updated')
            else:
                NetSipEntry.objects.create(
                    entry_type=entry_type,
                    amount=amount,
                    date=entry_date,
                    note=note,
                    created_by=request.user,
                )
                messages.success(request, 'Entry added')
            return redirect('clients:net_sip')
        except Exception as e:
            messages.error(request, f'Invalid input: {e}')

    try:
        year_picker_raw = request.GET.get('year_picker')
        selected_year = int(year_picker_raw) if year_picker_raw else date.today().year
    except Exception:
        selected_year = date.today().year

    gran = request.GET.get('granularity', 'month')
    series_mode = request.GET.get('series_mode', 'net')

    current_year = date.today().year
    entry_years = list(NetSipEntry.objects.values_list('date__year', flat=True).distinct())
    year_options = sorted(set(list(range(current_year + 1, current_year - 5, -1)) + entry_years), reverse=True)

    if gran == 'year':
        max_year = max(entry_years + [current_year]) if entry_years else current_year
        min_year = max_year - 4
        start = date(min_year, 1, 1)
        end = date(max_year, 12, 31)
    else:
        start = date(selected_year, 1, 1)
        end = date(selected_year, 12, 31)

    try:
        table_year = int(request.GET.get('table_year', selected_year))
    except Exception:
        table_year = selected_year

    entries_qs = NetSipEntry.objects.filter(date__range=(start, end))

    if gran == 'day':
        entries_grouped = entries_qs.annotate(period=TruncDay('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    elif gran == 'year':
        entries_grouped = entries_qs.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
    else:
        entries_grouped = entries_qs.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')

    data = {}
    for r in entries_grouped:
        key = r['period'].date() if hasattr(r['period'], 'date') else r['period']
        if key not in data:
            data[key] = {'fresh': 0, 'stopped': 0}
        if r['entry_type'] == 'fresh':
            data[key]['fresh'] = float(r['total'] or 0)
        else:
            data[key]['stopped'] = float(r['total'] or 0)

    period_totals = []
    if gran == 'year':
        min_year = start.year
        max_year = end.year
        for yr in range(min_year, max_year + 1):
            key = date(yr, 1, 1)
            fresh_total = data.get(key, {}).get('fresh', 0)
            stopped_total = data.get(key, {}).get('stopped', 0)
            net_total = fresh_total - stopped_total
            period_totals.append({
                'period': key.isoformat(),
                'label': str(yr),
                'fresh': round(fresh_total, 2),
                'stopped': round(stopped_total, 2),
                'net': round(net_total, 2),
            })
    else:
        periods = [date(start.year, m, 1) for m in range(1, 13)]
        for p in periods:
            fresh_total = data.get(p, {}).get('fresh', 0)
            stopped_total = data.get(p, {}).get('stopped', 0)
            net_total = fresh_total - stopped_total
            label = p.strftime('%b %Y') if gran == 'month' else p.strftime('%d %b %Y')
            period_totals.append({
                'period': p.isoformat(),
                'label': label,
                'fresh': round(fresh_total, 2),
                'stopped': round(stopped_total, 2),
                'net': round(net_total, 2),
            })

    if gran == 'year':
        table_entries = NetSipEntry.objects.filter(date__range=(start, end))
        table_grouped = table_entries.annotate(period=TruncYear('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(y, 1, 1): {'fresh': 0, 'stopped': 0} for y in range(start.year, end.year + 1)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'fresh': 0, 'stopped': 0}
            if r['entry_type'] == 'fresh':
                table_map[k]['fresh'] += float(r['total'] or 0)
            else:
                table_map[k]['stopped'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['fresh'] - val['stopped']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'fresh': val['fresh'], 'stopped': val['stopped'], 'net': net_val})
    else:
        table_entries = NetSipEntry.objects.filter(date__year=table_year)
        table_grouped = table_entries.annotate(period=TruncMonth('date')).values('period', 'entry_type').annotate(total=Sum('amount')).order_by('period')
        table_map = {date(table_year, m, 1): {'fresh': 0, 'stopped': 0} for m in range(1, 13)}
        for r in table_grouped:
            k = r['period'].date() if hasattr(r['period'], 'date') else r['period']
            if k not in table_map:
                table_map[k] = {'fresh': 0, 'stopped': 0}
            if r['entry_type'] == 'fresh':
                table_map[k]['fresh'] += float(r['total'] or 0)
            else:
                table_map[k]['stopped'] += float(r['total'] or 0)
        table_rows = []
        for k in sorted(table_map.keys()):
            val = table_map[k]
            label = k.strftime('%b %Y') if hasattr(k, 'strftime') else str(k)
            net_val = val['fresh'] - val['stopped']
            table_rows.append({'period': k.isoformat() if hasattr(k, 'isoformat') else str(k), 'label': label, 'fresh': val['fresh'], 'stopped': val['stopped'], 'net': net_val})

    entry_list = NetSipEntry.objects.order_by('-date', '-created_at')[:200]

    start_str = start.isoformat() if hasattr(start, 'isoformat') else str(start)
    end_str = end.isoformat() if hasattr(end, 'isoformat') else str(end)

    table_len = len(table_rows)
    fresh_sum = sum(r.get('fresh', 0) for r in table_rows)
    stopped_sum = sum(r.get('stopped', 0) for r in table_rows)
    net_sum = sum(r.get('net', 0) for r in table_rows)

    def _avg(total):
        return total / table_len if table_len else 0

    context = {
        'start': start,
        'end': end,
        'start_str': start_str,
        'end_str': end_str,
        'granularity': gran,
        'series_mode': series_mode,
        'period_totals_json': json.dumps(period_totals),
        'month_table': table_rows,
        'table_year': table_year,
        'year_options': year_options,
        'entry_list': entry_list,
        'table_totals': {
            'fresh_sum': fresh_sum,
            'stopped_sum': stopped_sum,
            'net_sum': net_sum,
            'fresh_avg': _avg(fresh_sum),
            'stopped_avg': _avg(stopped_sum),
            'net_avg': _avg(net_sum),
        },
    }

    return render(request, 'dashboards/net_sip.html', context)

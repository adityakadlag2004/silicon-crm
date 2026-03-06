"""Team views: list, add, edit, detail, delete, reset password for employees."""
import json
from itertools import cycle

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Sum, Count, Q

from django.db.models import Max

from ..models import Client, Sale, Employee, ManagerAccessConfig
from ..forms import EmployeeCreateForm, EmployeeDeactivateForm


def _next_employee_number():
    """Generate next incremental employee number like EMP001, EMP002, etc."""
    last = Employee.objects.filter(
        employee_number__isnull=False
    ).exclude(employee_number="").order_by("-id").values_list("employee_number", flat=True)
    max_num = 0
    for en in last:
        # Extract numeric part from e.g. 'EMP003' or plain '3'
        digits = ''.join(c for c in str(en) if c.isdigit())
        if digits:
            max_num = max(max_num, int(digits))
    return f"EMP{max_num + 1:03d}"


def _is_admin(request):
    user_emp = getattr(request.user, "employee", None)
    return request.user.is_superuser or (user_emp and user_emp.role == "admin")


@login_required
def team_list(request):
    """Full team listing with stats, search, filters."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")

    q = (request.GET.get("q") or "").strip()
    role_filter = request.GET.get("role", "")
    status_filter = request.GET.get("status", "")

    employees = Employee.objects.select_related("user").annotate(
        client_count=Count("client", distinct=True),
        total_sales=Count("sales", distinct=True),
        total_points=Sum("sales__points"),
    ).order_by("-active", "user__first_name", "user__username")

    if q:
        employees = employees.filter(
            Q(user__username__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__email__icontains=q)
            | Q(employee_number__icontains=q)
        )
    if role_filter:
        employees = employees.filter(role=role_filter)
    if status_filter == "active":
        employees = employees.filter(active=True)
    elif status_filter == "inactive":
        employees = employees.filter(active=False)

    # Summary stats
    total = Employee.objects.count()
    active_count = Employee.objects.filter(active=True).count()
    admins = Employee.objects.filter(role="admin", active=True).count()
    managers = Employee.objects.filter(role="manager", active=True).count()

    context = {
        "employees": employees,
        "q": q,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "total": total,
        "active_count": active_count,
        "inactive_count": total - active_count,
        "admins": admins,
        "managers": managers,
    }
    return render(request, "team/team_list.html", context)


@login_required
def team_add(request):
    """Add a new team member."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")

    if request.method == "POST":
        form = EmployeeCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = User.objects.create_user(
                    username=form.cleaned_data["username"],
                    email=form.cleaned_data.get("email", ""),
                    password=form.cleaned_data["password"],
                    first_name=request.POST.get("first_name", ""),
                    last_name=request.POST.get("last_name", ""),
                    is_active=True,
                )
                raw_num = request.POST.get("employee_number", "").strip()
                emp = Employee.objects.create(
                    user=user,
                    role=form.cleaned_data["role"],
                    salary=form.cleaned_data["salary"],
                    employee_number=raw_num if raw_num else _next_employee_number(),
                    active=True,
                )
            messages.success(request, f"Team member '{user.username}' created successfully.")
            return redirect("clients:team_list")
        else:
            for field, errs in form.errors.items():
                for e in errs:
                    messages.error(request, f"{field}: {e}")
    else:
        form = EmployeeCreateForm()

    return redirect("clients:team_list")


@login_required
def team_detail(request, employee_id):
    """Employee profile/detail page with stats."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")

    emp = get_object_or_404(Employee.objects.select_related("user"), id=employee_id)

    # Stats
    from django.utils import timezone
    from datetime import date
    today = date.today()

    total_sales = Sale.objects.filter(employee=emp).count()
    approved_sales = Sale.objects.filter(employee=emp, status="approved").count()
    pending_sales = Sale.objects.filter(employee=emp, status="pending").count()
    total_points = Sale.objects.filter(employee=emp, status="approved").aggregate(
        total=Sum("points")
    )["total"] or 0
    total_amount = Sale.objects.filter(employee=emp, status="approved").aggregate(
        total=Sum("amount")
    )["total"] or 0
    client_count = Client.objects.filter(mapped_to=emp).count()

    # This month stats
    month_sales = Sale.objects.filter(
        employee=emp, date__year=today.year, date__month=today.month
    ).count()
    month_amount = Sale.objects.filter(
        employee=emp, date__year=today.year, date__month=today.month, status="approved"
    ).aggregate(total=Sum("amount"))["total"] or 0
    month_points = Sale.objects.filter(
        employee=emp, date__year=today.year, date__month=today.month, status="approved"
    ).aggregate(total=Sum("points"))["total"] or 0

    # Recent sales
    recent_sales = Sale.objects.filter(employee=emp).select_related("client").order_by("-date", "-created_at")[:10]

    context = {
        "emp": emp,
        "total_sales": total_sales,
        "approved_sales": approved_sales,
        "pending_sales": pending_sales,
        "total_points": total_points,
        "total_amount": total_amount,
        "client_count": client_count,
        "month_sales": month_sales,
        "month_amount": month_amount,
        "month_points": month_points,
        "recent_sales": recent_sales,
    }
    return render(request, "team/team_detail.html", context)


@login_required
def team_edit(request, employee_id):
    """Edit employee profile."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")

    emp = get_object_or_404(Employee.objects.select_related("user"), id=employee_id)

    if request.method == "POST":
        user = emp.user
        user.first_name = request.POST.get("first_name", user.first_name)
        user.last_name = request.POST.get("last_name", user.last_name)
        user.email = request.POST.get("email", user.email)
        user.save(update_fields=["first_name", "last_name", "email"])

        emp.role = request.POST.get("role", emp.role)
        emp.salary = request.POST.get("salary", emp.salary)
        emp.employee_number = request.POST.get("employee_number", "").strip() or None
        emp.save(update_fields=["role", "salary", "employee_number"])

        messages.success(request, f"Updated {user.get_full_name() or user.username}.")
        return redirect("clients:team_detail", employee_id=emp.id)

    return render(request, "team/team_edit.html", {"emp": emp})


@login_required
@require_POST
def team_toggle_status(request, employee_id):
    """Activate/deactivate an employee (AJAX or form POST)."""
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)

    emp = get_object_or_404(Employee.objects.select_related("user"), id=employee_id)

    if emp.active:
        # Deactivate
        other_emps = list(Employee.objects.filter(active=True).exclude(id=emp.id))
        mapped_clients = list(Client.objects.filter(mapped_to=emp))

        if mapped_clients and not other_emps:
            msg = "Cannot deactivate: last active employee with mapped clients."
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"error": msg}, status=400)
            messages.error(request, msg)
            return redirect("clients:team_list")

        if other_emps:
            rr = cycle(other_emps)
            for client in mapped_clients:
                new_emp = next(rr)
                client.reassign_to(new_emp, changed_by=request.user, note="Auto-reassigned on deactivation")

        with transaction.atomic():
            emp.active = False
            emp.save(update_fields=["active"])
            if emp.user_id:
                emp.user.is_active = False
                emp.user.save(update_fields=["is_active"])

        msg = f"Deactivated {emp.user.username} and reassigned {len(mapped_clients)} clients."
    else:
        # Activate
        with transaction.atomic():
            emp.active = True
            emp.save(update_fields=["active"])
            if emp.user_id:
                emp.user.is_active = True
                emp.user.save(update_fields=["is_active"])

        msg = f"Reactivated {emp.user.username}."

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg, "active": emp.active})

    messages.success(request, msg)
    return redirect("clients:team_list")


@login_required
@require_POST
def team_delete(request, employee_id):
    """Permanently delete an employee."""
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)

    emp = get_object_or_404(Employee.objects.select_related("user"), id=employee_id)

    # Prevent deleting yourself
    if emp.user == request.user:
        msg = "You cannot delete your own account."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": msg}, status=400)
        messages.error(request, msg)
        return redirect("clients:team_list")

    # Reassign mapped clients
    other_emps = list(Employee.objects.filter(active=True).exclude(id=emp.id))
    mapped_clients = list(Client.objects.filter(mapped_to=emp))
    if mapped_clients and other_emps:
        rr = cycle(other_emps)
        for client in mapped_clients:
            new_emp = next(rr)
            client.reassign_to(new_emp, changed_by=request.user, note="Auto-reassigned on deletion")
    elif mapped_clients:
        Client.objects.filter(mapped_to=emp).update(mapped_to=None)

    username = emp.user.username
    user_obj = emp.user
    emp.delete()
    user_obj.delete()

    msg = f"Deleted employee '{username}' and reassigned {len(mapped_clients)} clients."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg})

    messages.success(request, msg)
    return redirect("clients:team_list")


@login_required
@require_POST
def team_reset_password(request, employee_id):
    """Reset an employee's password."""
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)

    emp = get_object_or_404(Employee.objects.select_related("user"), id=employee_id)
    new_password = request.POST.get("new_password", "").strip()
    if not new_password or len(new_password) < 6:
        msg = "Password must be at least 6 characters."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": msg}, status=400)
        messages.error(request, msg)
        return redirect("clients:team_edit", employee_id=emp.id)

    emp.user.set_password(new_password)
    emp.user.save()

    msg = f"Password reset for {emp.user.username}."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg})

    messages.success(request, msg)
    return redirect("clients:team_detail", employee_id=emp.id)

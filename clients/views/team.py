"""Team views: list, add, edit, detail, delete, reset password for employees."""
import json
from decimal import Decimal, InvalidOperation
from itertools import cycle

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Sum, Count, Q

from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from .. import permissions
from ..models import AuditLog, Client, Sale, Employee, EmployeeMilestone, ManagerAccessConfig
from ..forms import EmployeeAdminForm, EmployeeCreateForm, EmployeeDeactivateForm, MyProfileForm
from ..services import people
from .helpers import parse_date_param


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
    return permissions.is_admin(request.user)


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

    missed_count = len(people.missed())
    gap_count = len(people.incomplete_profiles())
    context = {
        "people_hub_url": reverse("clients:people_hub"),
        "missed_count": missed_count,
        "gap_count": gap_count,
        "upcoming_milestones": list(people.upcoming())[:5],
        "kpis": [
            {"label": "Team Members", "value": total, "color": "#4338CA"},
            {"label": "Active", "value": active_count, "color": "#15803D"},
            {"label": "Needs celebrating", "value": missed_count, "color": "#BE123C",
             "url": reverse("clients:people_hub")},
            {"label": "Incomplete profiles", "value": gap_count, "color": "#B45309",
             "url": reverse("clients:people_hub")},
        ],
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
        "crumbs": [{"label": "Team", "url": reverse("clients:team_list")},
                   {"label": emp.full_name}],
        "kpis": [
            {"label": "With the firm", "value": emp.tenure_display, "color": "#4338CA"},
            {"label": "Total experience", "value": emp.experience_display, "color": "#0369A1"},
            {"label": "Profile", "value": f"{emp.profile_completeness}%",
             "color": "#15803D" if emp.profile_is_complete else "#B45309"},
            {"label": "Clients", "value": Client.objects.filter(mapped_to=emp).count(),
             "color": "#7E22CE"},
        ],
        "milestones": emp.milestones.order_by("-occurs_on")[:8],
        "missing_admin": emp.missing_fields(include_admin=True),
        "reportees": emp.reportees.filter(active=True).select_related("user"),
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
        valid_roles = dict(Employee._meta.get_field("role").choices)
        new_role = (request.POST.get("role") or emp.role).strip()
        if new_role not in valid_roles:
            messages.error(request, f"Invalid role '{new_role}'.")
            return render(request, "team/team_edit.html", {
        "emp": emp,
        "domain_choices": Employee.Domain.choices,
        "managers": Employee.objects.filter(active=True).exclude(pk=emp.pk).select_related("user"),
    })

        raw_salary = (request.POST.get("salary") or "").strip()
        if raw_salary:
            try:
                new_salary = Decimal(raw_salary)
                if new_salary < 0:
                    raise InvalidOperation
            except InvalidOperation:
                messages.error(request, "Salary must be a non-negative number.")
                return render(request, "team/team_edit.html", {
        "emp": emp,
        "domain_choices": Employee.Domain.choices,
        "managers": Employee.objects.filter(active=True).exclude(pk=emp.pk).select_related("user"),
    })
        else:
            new_salary = emp.salary

        new_number = request.POST.get("employee_number", "").strip() or None
        if new_number and Employee.objects.exclude(pk=emp.pk).filter(employee_number=new_number).exists():
            messages.error(request, f"Employee number '{new_number}' is already in use.")
            return render(request, "team/team_edit.html", {
        "emp": emp,
        "domain_choices": Employee.Domain.choices,
        "managers": Employee.objects.filter(active=True).exclude(pk=emp.pk).select_related("user"),
    })

        user = emp.user
        user.first_name = request.POST.get("first_name", user.first_name)
        user.last_name = request.POST.get("last_name", user.last_name)
        user.email = request.POST.get("email", user.email)
        user.save(update_fields=["first_name", "last_name", "email"])

        old_role = emp.role
        emp.role = new_role
        emp.salary = new_salary
        emp.employee_number = new_number

        # Employment + personal fields. Blank means "leave it alone" for dates
        # so a half-filled form never wipes a known joining date.
        text_fields = [
            "first_name", "middle_name", "last_name", "position", "phone",
            "personal_email", "address", "blood_group", "qualification",
            "skills", "emergency_contact_name", "emergency_contact_phone",
            "emergency_contact_relation", "notes",
        ]
        for field in text_fields:
            if field in request.POST:
                setattr(emp, field, request.POST.get(field, "").strip())

        if request.POST.get("domain") in dict(Employee.Domain.choices):
            emp.domain = request.POST["domain"]
        for field in ("joining_date", "date_of_birth"):
            raw = (request.POST.get(field) or "").strip()
            if raw:
                parsed = parse_date_param(raw)
                if parsed:
                    setattr(emp, field, parsed)
        raw_prior = (request.POST.get("prior_experience_months") or "").strip()
        if raw_prior.isdigit():
            emp.prior_experience_months = int(raw_prior)
        raw_manager = (request.POST.get("reports_to") or "").strip()
        if raw_manager.isdigit():
            emp.reports_to = Employee.objects.filter(pk=int(raw_manager)).exclude(pk=emp.pk).first()
        elif raw_manager == "":
            emp.reports_to = None

        emp.save()
        # New dates mean new occasions to mark.
        people.generate_for(emp)

        if old_role != new_role:
            AuditLog.objects.create(
                action=AuditLog.ACTION_EMPLOYEE_ROLE_CHANGED,
                actor=request.user,
                target_model="Employee",
                target_id=emp.pk,
                summary=f"Role of '{user.username}' changed: {old_role} → {new_role}",
                details={"from": old_role, "to": new_role},
            )

        messages.success(request, f"Updated {user.get_full_name() or user.username}.")
        return redirect("clients:team_detail", employee_id=emp.id)

    return render(request, "team/team_edit.html", {
        "emp": emp,
        "domain_choices": Employee.Domain.choices,
        "managers": Employee.objects.filter(active=True).exclude(pk=emp.pk).select_related("user"),
    })


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

    # Employees with business history must be deactivated, not deleted —
    # sales and leads are protected records (on_delete=PROTECT would 500 anyway).
    sale_count = Sale.objects.filter(employee=emp).count()
    lead_count = emp.leads.count()
    if sale_count or lead_count:
        msg = (
            f"Cannot delete '{emp.user.username}': they have {sale_count} sale(s) "
            f"and {lead_count} lead(s) on record. Deactivate the employee instead."
        )
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
    try:
        # Enforce the same AUTH_PASSWORD_VALIDATORS as everywhere else.
        validate_password(new_password, user=emp.user)
    except ValidationError as e:
        msg = " ".join(e.messages)
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


# ─────────────────────────── my profile ───────────────────────────

@login_required
def my_profile(request):
    """Self-service profile — the page the dashboard prompt links to."""
    emp = getattr(request.user, "employee", None)
    if emp is None:
        messages.error(request, "You are not set up as a team member yet.")
        return redirect("clients:employee_dashboard")

    if request.method == "POST":
        form = MyProfileForm(request.POST, instance=emp)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.profile_updated_at = timezone.now()
            obj.save()
            # Newly-known dates mean new milestones to celebrate.
            people.generate_for(obj)
            messages.success(request, "Thanks — your profile is up to date.")
            return redirect("clients:my_profile")
    else:
        form = MyProfileForm(instance=emp)

    return render(request, "team/my_profile.html", {
        "page_title": "My Profile",
        "crumbs": [{"label": "My Profile"}],
        "kpis": [
            {"label": "Profile Complete", "value": f"{emp.profile_completeness}%",
             "color": "#15803D" if emp.profile_is_complete else "#B45309"},
            {"label": "With the firm", "value": emp.tenure_display, "color": "#4338CA"},
            {"label": "Total experience", "value": emp.experience_display, "color": "#0369A1"},
        ],
        "emp": emp,
        "form": form,
        "missing": emp.missing_fields(),
        "milestones": emp.milestones.order_by("-occurs_on")[:10],
    })


@login_required
def people_hub(request):
    """Admin view: who needs celebrating, and whose profile has gaps."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")

    missed = list(people.missed())
    upcoming = list(people.upcoming())
    gaps = people.incomplete_profiles()

    return render(request, "team/people_hub.html", {
        "page_title": "People",
        "crumbs": [{"label": "Team", "url": reverse("clients:team_list")},
                   {"label": "People"}],
        "kpis": [
            {"label": "Needs celebrating", "value": len(missed), "color": "#BE123C"},
            {"label": "Coming up (30d)", "value": len(upcoming), "color": "#B45309"},
            {"label": "Incomplete profiles", "value": len(gaps), "color": "#0369A1"},
            {"label": "Active team",
             "value": Employee.objects.filter(active=True).count(), "color": "#15803D"},
        ],
        "missed": missed,
        "upcoming": upcoming,
        "gaps": gaps,
    })


@login_required
@require_POST
def milestone_celebrate(request, milestone_id):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    milestone = get_object_or_404(EmployeeMilestone, pk=milestone_id)
    people.celebrate(milestone, request.user, (request.POST.get("note") or "").strip())
    messages.success(request, f"Marked — {milestone.employee.short_name} has been told.")
    return redirect(request.META.get("HTTP_REFERER") or "clients:people_hub")

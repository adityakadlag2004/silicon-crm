"""Sales views: add, list, approve, edit, delete, incentives, recalculate."""
from datetime import date
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.db.models import Q, Sum
from django.core.paginator import Paginator

from ..models import Client, Sale, Employee, IncentiveRule
from ..forms import AdminSaleForm, EditSaleForm, SaleForm
from .helpers import get_manager_access


@login_required
def add_sale(request):
    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            is_admin_user = request.user.is_superuser or (
                hasattr(request.user, "employee")
                and getattr(request.user.employee, "role", "") == "admin"
            )

            chosen_emp = form.cleaned_data.get("employee")
            if chosen_emp:
                sale.employee = chosen_emp
            else:
                sale.employee = getattr(request.user, "employee", None)

            if not sale.client:
                client_id = request.POST.get("client")
                if not client_id:
                    messages.error(request, "Please select a client from search results.")
                    return render(
                        request,
                        "sales/add_sale.html",
                        {
                            "form": form,
                            "employees": Employee.objects.select_related("user").all(),
                            "current_employee_id": getattr(request.user, "employee").id
                            if hasattr(request.user, "employee")
                            else None,
                        },
                    )
                try:
                    sale.client = Client.objects.get(id=client_id)
                except Client.DoesNotExist:
                    messages.error(request, "Selected client does not exist.")
                    return render(
                        request,
                        "sales/add_sale.html",
                        {
                            "form": form,
                            "employees": Employee.objects.select_related("user").all(),
                            "current_employee_id": getattr(request.user, "employee").id
                            if hasattr(request.user, "employee")
                            else None,
                        },
                    )

            sale.compute_points()

            if is_admin_user:
                sale.status = Sale.STATUS_APPROVED
                sale.approved_by = request.user
                sale.approved_at = timezone.now()
                sale.rejection_reason = ""
            else:
                sale.status = Sale.STATUS_PENDING
                sale.approved_by = None
                sale.approved_at = None
                sale.rejection_reason = ""

            sale.save()
            messages.success(request, "Sale added successfully!")
            return redirect("clients:all_sales")
    else:
        initial = {}
        if hasattr(request.user, "employee"):
            initial["employee"] = request.user.employee.id
            initial["date"] = date.today()
        form = AdminSaleForm(initial=initial)

    employees_qs = Employee.objects.select_related("user").all()
    current_emp_id = (
        getattr(request.user, "employee").id if hasattr(request.user, "employee") else None
    )
    return render(
        request,
        "sales/add_sale.html",
        {"form": form, "employees": employees_qs, "current_employee_id": current_emp_id},
    )


@login_required
def all_sales(request):
    sales_qs = Sale.objects.all().order_by("-date", "-created_at")

    user_emp = getattr(request.user, "employee", None)
    is_manager = bool(user_emp and user_emp.role == "manager")
    manager_access = get_manager_access() if is_manager else None

    if hasattr(request.user, "employee") and request.user.employee.role == "employee":
        sales_qs = sales_qs.filter(employee=request.user.employee)
    elif is_manager and manager_access and not manager_access.allow_view_all_sales:
        sales_qs = sales_qs.filter(employee=request.user.employee)

    product = request.GET.get("product")
    client = request.GET.get("client")
    employee = request.GET.get("employee")
    status = request.GET.get("status")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    q = (request.GET.get("q") or "").strip()

    if q:
        sales_qs = sales_qs.filter(
            Q(client__name__icontains=q)
            | Q(client__email__icontains=q)
            | Q(client__phone__icontains=q)
            | Q(employee__user__username__icontains=q)
            | Q(employee__user__first_name__icontains=q)
            | Q(employee__user__last_name__icontains=q)
            | Q(product__icontains=q)
        )

    if product:
        sales_qs = sales_qs.filter(product=product)
    if client:
        try:
            cid = int(client)
            sales_qs = sales_qs.filter(client_id=cid)
        except Exception:
            sales_qs = sales_qs.filter(client__name__icontains=client)
    if employee:
        sales_qs = sales_qs.filter(employee__user__username__icontains=employee)
    if status in [Sale.STATUS_PENDING, Sale.STATUS_APPROVED, Sale.STATUS_REJECTED]:
        sales_qs = sales_qs.filter(status=status)
    if start_date and end_date:
        sales_qs = sales_qs.filter(date__range=[start_date, end_date])

    if not (product or client or employee or start_date or end_date or q):
        sales_qs = sales_qs.filter(date=date.today())

    paginator = Paginator(sales_qs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    qdict = request.GET.copy()
    qdict.pop("page", None)
    qstring = qdict.urlencode()

    context = {
        "sales": page_obj,
        "is_employee": hasattr(request.user, "employee") and request.user.employee.role == "employee",
        "is_manager": is_manager,
        "manager_can_edit": bool(is_manager and manager_access and manager_access.allow_edit_sales),
        "qstring": qstring,
        "q": q,
        "status": status,
    }
    return render(request, "sales/all_sales.html", context)


@login_required
def admin_add_sale(request):
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return redirect("clients:employee_dashboard")

    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            if not sale.employee_id:
                form.add_error("employee", "Please select an employee for this sale.")
            else:
                sale.compute_points()
                sale.status = Sale.STATUS_APPROVED
                sale.approved_by = request.user
                sale.approved_at = timezone.now()
                sale.rejection_reason = ""
                sale.save()
                messages.success(request, "Sale added successfully!")
                return redirect("clients:all_sales")
    else:
        form = AdminSaleForm()

    return render(request, "sales/admin_add_sale.html", {"form": form})


@login_required
def approve_sales(request):
    user_emp = getattr(request.user, "employee", None)
    is_admin = request.user.is_superuser or (user_emp and user_emp.role == "admin")
    is_manager = bool(user_emp and user_emp.role == "manager")
    manager_access = get_manager_access() if is_manager else None

    if not (is_admin or (manager_access and manager_access.allow_approve_sales)):
        return HttpResponseForbidden("You do not have permission to approve sales.")

    if request.method == "POST":
        action = request.POST.get("action")
        sale_id = request.POST.get("sale_id")
        reason = (request.POST.get("reason") or "").strip()
        sale = get_object_or_404(Sale, id=sale_id)
        if action == "approve":
            sale.status = Sale.STATUS_APPROVED
            sale.approved_by = request.user
            sale.approved_at = timezone.now()
            sale.rejection_reason = ""
            sale.save()
            messages.success(request, f"Approved sale #{sale.id}.")
        elif action == "reject":
            sale.status = Sale.STATUS_REJECTED
            sale.approved_by = request.user
            sale.approved_at = timezone.now()
            sale.rejection_reason = reason
            sale.save()
            messages.info(request, f"Rejected sale #{sale.id}.")
        return redirect("clients:approve_sales")

    employee_filter = request.GET.get("employee", "").strip()
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    sales_qs = Sale.objects.filter(status=Sale.STATUS_PENDING).select_related("client", "employee__user")
    if manager_access and not manager_access.allow_view_all_sales:
        sales_qs = sales_qs.filter(employee=user_emp)
    if employee_filter:
        sales_qs = sales_qs.filter(
            Q(employee__user__username__icontains=employee_filter)
            | Q(employee__user__first_name__icontains=employee_filter)
            | Q(employee__user__last_name__icontains=employee_filter)
        )
    if start_date and end_date:
        sales_qs = sales_qs.filter(date__range=[start_date, end_date])

    sales_qs = sales_qs.order_by("-date", "-created_at")
    context = {
        "sales": sales_qs,
        "employee_filter": employee_filter,
        "start_date": start_date,
        "end_date": end_date,
    }
    return render(request, "sales/approve_sales.html", context)


@login_required
def manage_incentive_rules(request):
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        messages.error(request, "You do not have permission to access incentive rules.")
        return redirect("clients:admin_dashboard")

    rules = IncentiveRule.objects.all()

    if request.method == "POST":
        for rule in rules:
            unit_field = f"unit_{rule.id}"
            points_field = f"points_{rule.id}"
            active_field = f"active_{rule.id}"
            if unit_field in request.POST and points_field in request.POST:
                rule.unit_amount = request.POST[unit_field]
                rule.points_per_unit = request.POST[points_field]
                if rule.product == "Life Insurance":
                    rule.active = bool(request.POST.get(active_field))
                rule.save()
        messages.success(request, "Incentive rules updated successfully!")
        return redirect("clients:manage_incentive_rules")

    return render(request, "incentives/manage_rules.html", {"rules": rules})


@login_required
def recalc_points(request):
    user_emp = getattr(request.user, "employee", None)
    if request.user.is_superuser or (user_emp and user_emp.role == "admin"):
        sales = Sale.objects.all()
    elif user_emp:
        sales = Sale.objects.filter(employee=user_emp)
    else:
        messages.error(request, "You are not mapped to an employee.")
        return redirect("clients:login")

    count = 0
    for s in sales:
        s.compute_points()
        s.save()
        count += 1

    messages.success(request, f"Recalculated points for {count} sales.")
    if request.user.employee.role == "admin":
        return redirect("clients:all_sales")
    else:
        return redirect("clients:employee_dashboard")


@login_required
def edit_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    user_emp = getattr(request.user, "employee", None)
    is_admin_user = request.user.is_superuser or (user_emp and user_emp.role == "admin")
    is_manager = bool(user_emp and user_emp.role == "manager")
    mgr_access = get_manager_access() if is_manager else None
    if (
        not is_admin_user
        and not (is_manager and mgr_access and mgr_access.allow_edit_sales)
        and (not user_emp or sale.employee != user_emp)
    ):
        return HttpResponseForbidden("You do not have permission to edit this sale.")

    if request.method == "POST":
        form = EditSaleForm(request.POST, instance=sale)
        if form.is_valid():
            form.save()
            messages.success(request, "Sale updated successfully!")
            return redirect("clients:all_sales")
    else:
        form = EditSaleForm(instance=sale)

    return render(request, "sales/edit_sale.html", {"form": form, "sale": sale})


@login_required
def delete_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    user_emp = getattr(request.user, "employee", None)
    is_admin_user = request.user.is_superuser or (user_emp and user_emp.role == "admin")
    if not is_admin_user and (not user_emp or sale.employee != user_emp):
        return HttpResponseForbidden("You do not have permission to delete this sale.")
    if request.method == "POST":
        sale.delete()
        messages.success(request, "Sale deleted successfully!")
        return redirect("clients:admin_dashboard")
    return render(request, "sales/delete_sale.html", {"sale": sale})

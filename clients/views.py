# clients/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Value, DecimalField
from datetime import date
from .models import Client, Sale, MonthlyIncentive,Employee
from .forms import SaleForm,AdminSaleForm,EditSaleForm
from django import forms


from .models import Sale








def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)

            # 🔥 Auto-recalc points for this user
            if hasattr(user, "employee"):
                if user.employee.role == "admin":
                    sales = Sale.objects.all()
                else:
                    sales = Sale.objects.filter(employee=user.employee)
                for s in sales:
                    s.compute_points()
                    s.save()

            # Redirect based on role
            if hasattr(user, "employee"):
                role = user.employee.role.lower()
                if role == "admin":
                    return redirect("admin_dashboard")
                elif role == "employee":
                    return redirect("employee_dashboard")
            else:
                messages.error(request, "No employee role mapped.")
        else:
            messages.error(request, "Invalid username or password")

    # ✅ Always return a response on GET or failed POST
    return render(request, "login.html")


@login_required
def logout_view(request):
    logout(request)
    return redirect("login")

# Client Form
class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "email", "phone", "pan", "address", "mapped_to",
                  "sip_status", "sip_amount", "sip_topup",
                  "health_status", "health_cover", "health_topup", "health_product",
                  "life_status", "life_cover", "life_product",
                  "motor_status", "motor_insured_value", "motor_product",
                  "pms_status", "pms_amount", "pms_start_date"]

from django.db.models import Sum, Value
from django.db.models.functions import Coalesce

from django.db.models import Sum
from django.utils.timezone import now
from datetime import datetime

from django.db.models import Sum
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Sale, Client

@login_required
def admin_dashboard(request):
    sales = Sale.objects.all()

    # Overall summary
    total_clients = Client.objects.count()
    total_sales = sales.aggregate(total=Sum("amount"))["total"] or 0
    total_points = sales.aggregate(total=Sum("points"))["total"] or 0

    # 🔹 Product-wise totals
    sip_sales = sales.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = sales.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = sales.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = sales.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = sales.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = sales.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    context = {
        "total_clients": total_clients,
        "total_sales": total_sales,
        "total_points": total_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
    }
    return render(request, "dashboards/admin_dashboard.html", context)


@login_required
def employee_dashboard(request):
    emp = request.user.employee
    sales = Sale.objects.filter(employee=emp)

    # Totals for this employee
    total_sales = sales.aggregate(total=Sum("amount"))["total"] or 0
    total_points = sales.aggregate(total=Sum("points"))["total"] or 0

    # Product-wise totals
    sip_sales = sales.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = sales.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = sales.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = sales.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = sales.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = sales.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    context = {
        "total_sales": total_sales,
        "total_points": total_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
    }
    return render(request, "dashboards/employee_dashboard.html", context)

@login_required
def add_sale(request):
    if request.method == "POST":
        form = SaleForm(request.POST, employee=request.user.employee)
        if form.is_valid():
            sale = form.save(commit=False)
            # always assign employee
            sale.employee = request.user.employee
            try:
                sale.save()
                messages.success(request, "Sale added successfully!")
                return redirect("employee_dashboard")
            except Exception as e:
                messages.error(request, f"Error saving sale: {e}")
        else:
            messages.error(request, f"Form errors: {form.errors}")
    else:
        form = SaleForm(employee=request.user.employee)

    return render(request, "sales/add_sale.html", {"form": form})

@login_required
def my_clients(request):
    clients = Client.objects.filter(mapped_to=request.user.employee)
    return render(request, "clients/my_clients.html", {"clients": clients})

@login_required
def all_clients(request):
    clients = Client.objects.select_related("mapped_to")
    return render(request, "clients/all_clients.html", {"clients": clients})


@login_required
def add_client(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Client added successfully!")
            return redirect("all_clients")
    else:
        form = ClientForm()
    return render(request, "clients/add_client.html", {"form": form})

from django.db.models import Q

@login_required
def all_sales(request):
    sales = Sale.objects.all().order_by("-date")

    # Apply filters
    product = request.GET.get("product")
    client = request.GET.get("client")
    employee = request.GET.get("employee")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if product:
        sales = sales.filter(product=product)
    if client:
        sales = sales.filter(client_id=client)
    if employee:
        sales = sales.filter(employee__user__username__icontains=employee)
    if start_date and end_date:
        sales = sales.filter(date__range=[start_date, end_date])

    context = {
        "sales": sales,
    }
    return render(request, "sales/all_sales.html", context)


@login_required
def admin_add_sale(request):
    if not request.user.is_superuser and request.user.employee.role != "admin":
        return redirect("employee_dashboard")  # block non-admins

    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            # Make sure employee is set
            if not sale.employee:
                form.add_error("employee", "Please select an employee.")
            else:
                sale.save()
                messages.success(request, "Sale added successfully!")
                return redirect("admin_dashboard")
    else:
        form = AdminSaleForm()

    return render(request, "sales/admin_add_sale.html", {"form": form})



from .models import IncentiveRule

@login_required
def manage_incentive_rules(request):
    # Role check OR hardcoded pass
    if request.user.employee.role != "admin" and request.GET.get("pass") != "SuperSecret123":
        messages.error(request, "You do not have permission to access incentive rules.")
        return redirect("admin_dashboard")

    rules = IncentiveRule.objects.all()

    if request.method == "POST":
        for rule in rules:
            unit_field = f"unit_{rule.id}"
            points_field = f"points_{rule.id}"
            if unit_field in request.POST and points_field in request.POST:
                rule.unit_amount = request.POST[unit_field]
                rule.points_per_unit = request.POST[points_field]
                rule.save()
        messages.success(request, "Incentive rules updated successfully!")
        return redirect("manage_incentive_rules")

    return render(request, "incentives/manage_rules.html", {"rules": rules})


@login_required
def recalc_points(request):
    # Admin sees all sales, Employee sees only their own
    if request.user.employee.role == "admin":
        sales = Sale.objects.all()
    else:
        sales = Sale.objects.filter(employee=request.user.employee)

    count = 0
    for s in sales:
        s.compute_points()   # use IncentiveRule
        s.save()
        count += 1

    messages.success(request, f"Recalculated points for {count} sales.")
    if request.user.employee.role == "admin":
        return redirect("all_sales")
    else:
        return redirect("employee_dashboard")




from django.db.models import Q
from django.http import JsonResponse

@login_required
def search_clients(request):
    query = request.GET.get("q", "")
    clients = Client.objects.filter(
        Q(name__icontains=query) |
        Q(email__icontains=query) |
        Q(phone__icontains=query)
    )[:10]

    results = [
        {"id": c.id, "text": f"{c.name} ({c.email or ''} {c.phone or ''})"}
        for c in clients
    ]
    return JsonResponse({"results": results})


from decimal import Decimal

@login_required
def edit_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)

    if request.method == "POST":
        form = EditSaleForm(request.POST, instance=sale)
        if form.is_valid():
            form.save()
            messages.success(request, "Sale updated successfully!")
            return redirect("all_sales")
    else:
        form = EditSaleForm(instance=sale)

    return render(request, "sales/edit_sale.html", {"form": form, "sale": sale})



@login_required
def delete_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    if request.method == "POST":
        sale.delete()
        messages.success(request, "Sale deleted successfully!")
        return redirect("admin_dashboard")
    return render(request, "sales/delete_sale.html", {"sale": sale})


def save_model(self, request, obj, form, change):
    if not request.user.is_superuser:
        emp = getattr(request.user, "employee", None)
        if emp:
            obj.employee = emp
    super().save_model(request, obj, form, change)

    # 🔹 Update client status based on this sale
    client = obj.client
    if obj.product == "SIP":
        client.sip_status = True
        client.sip_amount = (client.sip_amount or 0) + obj.amount
    elif obj.product == "Lumsum":
        # You may want to track lumpsum in SIP or separately
        pass
    elif obj.product == "Life Insurance":
        client.life_status = True
        client.life_cover = (client.life_cover or 0) + obj.amount
    elif obj.product == "Health Insurance":
        client.health_status = True
        client.health_cover = (client.health_cover or 0) + obj.amount
    elif obj.product == "Motor Insurance":
        client.motor_status = True
        client.motor_insured_value = (client.motor_insured_value or 0) + obj.amount
    elif obj.product == "PMS":
        client.pms_status = True
        client.pms_amount = (client.pms_amount or 0) + obj.amount
        if not client.pms_start_date:
            client.pms_start_date = obj.date

    client.save()

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

@receiver([post_save, post_delete], sender=Sale)
def update_client_status(sender, instance, **kwargs):
    client = instance.client
    # Recalculate totals from all sales
    sales = Sale.objects.filter(client=client)

    client.sip_amount = sales.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    client.life_cover = sales.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.health_cover = sales.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.motor_insured_value = sales.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.pms_amount = sales.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    client.sip_status = client.sip_amount > 0
    client.life_status = client.life_cover > 0
    client.health_status = client.health_cover > 0
    client.motor_status = client.motor_insured_value > 0
    client.pms_status = client.pms_amount > 0

    client.save()

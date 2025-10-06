# clients/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Value, DecimalField
from datetime import date
from .models import Client, Sale, CalendarEvent,Employee
from .forms import SaleForm,AdminSaleForm,EditSaleForm
from django.http import HttpResponseRedirect
from django import forms
from django.utils import timezone  
from django.http import HttpResponse
from django.utils.timezone import now
from django.db.models import Sum
from .models import Sale, Client, Target
from django.contrib.auth.decorators import login_required
from .models import Employee, Sale, Target, MonthlyTargetHistory,CallRecord
import json
from calendar import month_name



from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from .models import Sale  # make sure Sale is imported

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

            # ✅ Redirect based on role
            if hasattr(user, "employee"):
                role = user.employee.role.lower()
                if role == "admin":
                    return redirect("clients:admin_dashboard")   # fixed
                elif role == "employee":
                    return redirect("clients:employee_dashboard")  # fixed
            else:
                messages.error(request, "No employee role mapped.")
        else:
            messages.error(request, "Invalid username or password")

    # ✅ Always return a response on GET or failed POST
    return render(request, "login.html")



@login_required
def logout_view(request):
    logout(request)
    return redirect("clients:login")

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
    today = timezone.now().date()
    month = today.month
    year = today.year

    # All-time sales (if you use it elsewhere)
    all_sales_qs = Sale.objects.all()

    # --- IMPORTANT: use month-scoped queryset for 'This Month' widgets ---
    monthly_sales_qs = Sale.objects.filter(created_at__year=year, created_at__month=month)

    # ---------------- Overall summary ----------------
    total_clients = Client.objects.count()
    # If "Total Sales (This Month)" is what you want in the card, use monthly_sales_qs
    total_sales = monthly_sales_qs.aggregate(total=Sum("amount"))["total"] or 0
    total_points = monthly_sales_qs.aggregate(total=Sum("points"))["total"] or 0

    # ---------------- Product-wise totals (THIS MONTH) ----------------
    sip_sales = monthly_sales_qs.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = monthly_sales_qs.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = monthly_sales_qs.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = monthly_sales_qs.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = monthly_sales_qs.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = monthly_sales_qs.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    # ---------------- Section 1: Today's Summary ----------------
    todays_summary = []
    for emp in Employee.objects.all():
        emp_sales = monthly_sales_qs.filter(employee=emp, created_at__date=today)
        todays_summary.append({
            "employee": emp.user.username if hasattr(emp, "user") else emp.name,
            "sales": emp_sales.aggregate(total=Sum("amount"))["total"] or 0,
            "points": emp_sales.aggregate(total=Sum("points"))["total"] or 0,
            "new_clients": Client.objects.filter(mapped_to=emp, created_at__date=today).count(),
        })

    # ---------------- Section 2: Monthly Progress (Employee × Product) ----------------
    PRODUCT_MAP = {
        "SIP": "sip",
        "Lumsum": "lumpsum",
        "Life Insurance": "life",
        "Health Insurance": "health",
        "Motor Insurance": "motor",
        "PMS": "pms",
    }

    monthly_progress = []
    for emp in Employee.objects.all():
        row = {"employee": emp.user.username if hasattr(emp, "user") else emp.name}
        total_emp_sales = 0
        for product, key in PRODUCT_MAP.items():
            amt = monthly_sales_qs.filter(
                employee=emp,
                product=product,
            ).aggregate(total=Sum("amount"))["total"] or 0
            row[key] = amt
            total_emp_sales += amt
        row["total"] = total_emp_sales
        monthly_progress.append(row)

    # ---------------- Section 3: Monthly Cumulative Summary ----------------
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

    # ---------------- Context ----------------
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
        "todays_summary": todays_summary,
        "monthly_progress": monthly_progress,
        "monthly_summary": monthly_summary,
    }

    return render(request, "dashboards/admin_dashboard.html", context)


@login_required
def employee_dashboard(request):
    emp = request.user.employee
    today = now().date()

    # --- Querysets restricted by time ---
    monthly_sales_qs = Sale.objects.filter(
        employee=emp,
        date__year=today.year,
        date__month=today.month
    )
    today_sales_qs = monthly_sales_qs.filter(date=today)

    # --- Totals for this employee (THIS MONTH only) ---
    total_sales = monthly_sales_qs.aggregate(total=Sum("amount"))["total"] or 0
    total_points = monthly_sales_qs.aggregate(total=Sum("points"))["total"] or 0

    # --- Product-wise totals (THIS MONTH only) ---
    sip_sales = monthly_sales_qs.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = monthly_sales_qs.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = monthly_sales_qs.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = monthly_sales_qs.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = monthly_sales_qs.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = monthly_sales_qs.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    # --- Today's sales (resets daily) ---
    today_sales = today_sales_qs.values("product").annotate(total=Sum("amount"))
    today_sales_dict = {s["product"]: s["total"] for s in today_sales}
    today = timezone.now().date()
    todays_tasks = CalendarEvent.objects.filter(
    employee=request.user.employee,
    scheduled_time__date=today,
    ).order_by("scheduled_time")

    # --- Monthly sales (resets monthly) ---
    month_sales = monthly_sales_qs.values("product").annotate(total=Sum("amount"))
    month_sales_dict = {s["product"]: s["total"] for s in month_sales}

    # --- Global targets ---
    daily_targets = Target.objects.filter(target_type="daily")
    monthly_targets = Target.objects.filter(target_type="monthly")

    # --- Attach progress to each target ---
    for target in daily_targets:
        achieved = today_sales_dict.get(target.product, 0)
        target.achieved = achieved
        target.progress = (achieved / target.target_value * 100) if target.target_value else 0

    for target in monthly_targets:
        achieved = month_sales_dict.get(target.product, 0)
        target.achieved = achieved
        target.progress = (achieved / target.target_value * 100) if target.target_value else 0

    # --- Past 6 months performance history ---
    history = MonthlyTargetHistory.objects.filter(employee=emp).order_by("-year", "-month")[:6]

    
    todays_events = CalendarEvent.objects.filter(
    employee=request.user.employee,
    scheduled_time__date=today,
    status="pending",   # 👈 only show pending
).order_by("scheduled_time")

    context = {
        "total_sales": total_sales,
        "total_points": total_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "todays_events": todays_events,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "today_sales_dict": today_sales_dict,
        "month_sales_dict": month_sales_dict,
        "daily_targets": daily_targets,
        "monthly_targets": monthly_targets,
        "history": history,   # 👈 important for template
    }
    return render(request, "dashboards/employee_dashboard.html", context)


@login_required
def add_sale(request):
    if request.method == "POST":
        form = SaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)

            # ✅ assign logged-in employee
            sale.employee = request.user.employee  

            # ✅ assign client from hidden field
            client_id = request.POST.get("client")
            if not client_id:
                messages.error(request, "Please select a client from search results.")
                return render(request, "sales/add_sale.html", {"form": form})

            try:
                client = Client.objects.get(id=client_id)
            except Client.DoesNotExist:
                messages.error(request, "Selected client does not exist.")
                return render(request, "sales/add_sale.html", {"form": form})

            sale.client = client

            # compute points before saving
            sale.compute_points()
            sale.save()

            messages.success(request, "Sale added successfully!")
            return redirect("clients:all_sales")
    else:
        form = SaleForm()

    return render(request, "sales/add_sale.html", {"form": form})
# clients/views.py
from django.db.models import Q

@login_required
def all_clients(request):
    clients = Client.objects.select_related("mapped_to")

    # Filters
    sip_status = request.GET.get("sip_status")
    pms_status = request.GET.get("pms_status")
    life_status = request.GET.get("life_status")
    health_status = request.GET.get("health_status")

    sip_min = request.GET.get("sip_min")
    sip_max = request.GET.get("sip_max")
    pms_min = request.GET.get("pms_min")
    pms_max = request.GET.get("pms_max")
    life_min = request.GET.get("life_min")
    life_max = request.GET.get("life_max")
    health_min = request.GET.get("health_min")
    health_max = request.GET.get("health_max")

    # Status filters
    if sip_status in ["yes", "no"]:
        clients = clients.filter(sip_status=(sip_status == "yes"))
    if pms_status in ["yes", "no"]:
        clients = clients.filter(pms_status=(pms_status == "yes"))
    if life_status in ["yes", "no"]:
        clients = clients.filter(life_status=(life_status == "yes"))
    if health_status in ["yes", "no"]:
        clients = clients.filter(health_status=(health_status == "yes"))

    # Range filters
    if sip_min: clients = clients.filter(sip_amount__gte=sip_min)
    if sip_max: clients = clients.filter(sip_amount__lte=sip_max)
    if pms_min: clients = clients.filter(pms_amount__gte=pms_min)
    if pms_max: clients = clients.filter(pms_amount__lte=pms_max)
    if life_min: clients = clients.filter(life_cover__gte=life_min)
    if life_max: clients = clients.filter(life_cover__lte=life_max)
    if health_min: clients = clients.filter(health_cover__gte=health_min)
    if health_max: clients = clients.filter(health_cover__lte=health_max)

    return render(request, "clients/all_clients.html", {"clients": clients})


@login_required
def my_clients(request):
    clients = Client.objects.filter(mapped_to=request.user.employee)

    # Apply same filters (reuse above logic)
    sip_status = request.GET.get("sip_status")
    pms_status = request.GET.get("pms_status")
    life_status = request.GET.get("life_status")
    health_status = request.GET.get("health_status")

    sip_min = request.GET.get("sip_min")
    sip_max = request.GET.get("sip_max")
    pms_min = request.GET.get("pms_min")
    pms_max = request.GET.get("pms_max")
    life_min = request.GET.get("life_min")
    life_max = request.GET.get("life_max")
    health_min = request.GET.get("health_min")
    health_max = request.GET.get("health_max")

    if sip_status in ["yes", "no"]:
        clients = clients.filter(sip_status=(sip_status == "yes"))
    if pms_status in ["yes", "no"]:
        clients = clients.filter(pms_status=(pms_status == "yes"))
    if life_status in ["yes", "no"]:
        clients = clients.filter(life_status=(life_status == "yes"))
    if health_status in ["yes", "no"]:
        clients = clients.filter(health_status=(health_status == "yes"))

    if sip_min: clients = clients.filter(sip_amount__gte=sip_min)
    if sip_max: clients = clients.filter(sip_amount__lte=sip_max)
    if pms_min: clients = clients.filter(pms_amount__gte=pms_min)
    if pms_max: clients = clients.filter(pms_amount__lte=pms_max)
    if life_min: clients = clients.filter(life_cover__gte=life_min)
    if life_max: clients = clients.filter(life_cover__lte=life_max)
    if health_min: clients = clients.filter(health_cover__gte=health_min)
    if health_max: clients = clients.filter(health_cover__lte=health_max)

    return render(request, "clients/my_clients.html", {"clients": clients})



@login_required
def add_client(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Client added successfully!")
            return redirect("clients:all_clients")  # ✅ namespaced
    else:
        form = ClientForm()
    return render(request, "clients/add_client.html", {"form": form})

from django.db.models import Q

from django.core.paginator import Paginator

@login_required
def all_sales(request):
    sales = Sale.objects.all().order_by("-date", "-created_at")

    # Role-based filtering
    if request.user.employee.role == "employee":
        sales = sales.filter(employee=request.user.employee)

    # Apply filters only if user submits something
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

    # Default: show only today's sales unless filters are applied
    if not (product or client or employee or start_date or end_date):
        sales = sales.filter(date=date.today())

    # Pagination
    paginator = Paginator(sales, 10)  # 10 records per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "sales": page_obj,
        "is_employee": request.user.employee.role == "employee",
    }
    return render(request, "sales/all_sales.html", context)


@login_required
def admin_add_sale(request):
    if not request.user.is_superuser and request.user.employee.role != "admin":
        return redirect("clients:employee_dashboard")  # block non-admins

    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)

            # 🔹 Ensure employee is assigned
            if not sale.employee_id:
                form.add_error("employee", "Please select an employee for this sale.")
            else:
                sale.save()
                messages.success(request, "Sale added successfully!")
                return redirect("clients:all_sales")  # show latest sales immediately
    else:
        form = AdminSaleForm()

    return render(request, "sales/admin_add_sale.html", {"form": form})





from .models import IncentiveRule

@login_required
def manage_incentive_rules(request):
    # Role check OR hardcoded pass
    if request.user.employee.role != "admin" and request.GET.get("pass") != "SuperSecret123":
        messages.error(request, "You do not have permission to access incentive rules.")
        return redirect("clients:admin_dashboard")

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
        return redirect("clients:manage_incentive_rules")

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
        return redirect("clients:all_sales")
    else:
        return redirect("clients:employee_dashboard")




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
            return redirect("clients:all_sales")
    else:
        form = EditSaleForm(instance=sale)

    return render(request, "sales/edit_sale.html", {"form": form, "sale": sale})



@login_required
def delete_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    if request.method == "POST":
        sale.delete()
        messages.success(request, "Sale deleted successfully!")
        return redirect("clients:admin_dashboard")
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


@login_required
def client_analysis(request):
    # Base queryset
    if request.user.employee.role == "admin":
        clients = Client.objects.all()
    else:
        clients = Client.objects.filter(mapped_to=request.user.employee)

    # Apply filters
    filters = {}
    if request.GET.get("sip_status") in ["yes", "no"]:
        filters["sip_status"] = True if request.GET["sip_status"] == "yes" else False
    if request.GET.get("life_status") in ["yes", "no"]:
        filters["life_status"] = True if request.GET["life_status"] == "yes" else False
    if request.GET.get("health_status") in ["yes", "no"]:
        filters["health_status"] = True if request.GET["health_status"] == "yes" else False
    if request.GET.get("motor_status") in ["yes", "no"]:
        filters["motor_status"] = True if request.GET["motor_status"] == "yes" else False
    if request.GET.get("pms_status") in ["yes", "no"]:
        filters["pms_status"] = True if request.GET["pms_status"] == "yes" else False

    clients = clients.filter(**filters)

    # Date filter
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    if start_date and end_date:
        clients = clients.filter(created_at__range=[start_date, end_date])

    # Export
    if "export" in request.GET:
        import csv
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="clients_analysis.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Name", "Email", "Phone", "SIP", "Life", "Health", "Motor", "PMS", "Created At"])
        for c in clients:
            writer.writerow([
                c.id, c.name, c.email, c.phone,
                "Yes" if c.sip_status else "No",
                "Yes" if c.life_status else "No",
                "Yes" if c.health_status else "No",
                "Yes" if c.motor_status else "No",
                "Yes" if c.pms_status else "No",
                c.created_at.strftime("%Y-%m-%d"),
            ])
        return response

    return render(request, "clients/client_analysis.html", {"clients": clients})


# clients/views.py
from django.contrib.auth.decorators import user_passes_test

def is_admin(user):
    return hasattr(user, "employee") and user.employee.role == "admin"

@user_passes_test(is_admin)
def map_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)
    employees = Employee.objects.all()

    if request.method == "POST":
        emp_id = request.POST.get("employee")
        if emp_id:
            employee = get_object_or_404(Employee, id=emp_id)
            client.mapped_to = employee
            client.status = "Mapped"
            client.save()
            messages.success(request, f"Client {client.name} mapped to {employee.user.username}")
        else:
            client.mapped_to = None
            client.status = "Unmapped"
            client.save()
            messages.success(request, f"Client {client.name} unmapped")

        return redirect("clients:all_clients")

    return render(request, "clients/map_client.html", {"client": client, "employees": employees})




def _last_n_months(today, n=12):
    """Return list of (year, month) tuples from oldest -> newest (n months including current)."""
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append((y, m))
        if m == 1:
            m = 12
            y -= 1
        else:
            m -= 1
    months.reverse()
    return months

import json
from calendar import month_name
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum
from clients.models import MonthlyTargetHistory



@login_required
def employee_past_performance(request):
    """
    Page: shows a Chart.js line chart of monthly points (last 12 months)
    and a clickable list of months (each links to month detail).
    """
    emp = request.user.employee
    today = now().date()


    months = _last_n_months(today, n=12)
    

    labels = []
    points_data = []
    months_data = []  # list of dicts to render table rows

    for (y, m) in months:
        label = f"{month_name[m]} {y}"
        # Sum points for this employee in that month
        pts = (
            Sale.objects.filter(employee=emp, date__year=y, date__month=m)
            .aggregate(total=Sum("points"))["total"]
            or 0
        )

        labels.append(label)
        points_data.append(int(pts))
        months_data.append({"year": y, "month": m, "label": label, "points": int(pts)})

    context = {
        "labels_json": json.dumps(labels),
        "points_json": json.dumps(points_data),
        "months_data": months_data,
    }
    return render(request, "dashboards/employee_past_performance.html", context)


@login_required
def past_month_performance(request, year, month):
    """
    Shows product-wise business done for this employee in the specific month.
    product rows will include: product name, total_amount, total_points, (and monthly target & achieved if available)
    """
    emp = request.user.employee

    # product-wise sales in the month
    product_sales = (
        Sale.objects.filter(employee=emp, date__year=year, date__month=month)
        .values("product")
        .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        .order_by("-total_amount")
    )

    # Also fetch MonthlyTargetHistory rows (if you want to show target_value & achieved_value)
    target_history = MonthlyTargetHistory.objects.filter(employee=emp, year=year, month=month)
    target_map = {t.product: t for t in target_history}

    products = []
    for row in product_sales:
        prod = row["product"]
        prod_row = {
            "product": prod,
            "total_amount": row["total_amount"] or 0,
            "total_points": int(row["total_points"] or 0),
            "target_value": target_map.get(prod).target_value if prod in target_map else None,
            "achieved_value": target_map.get(prod).achieved_value if prod in target_map else None,
        }
        products.append(prod_row)

    context = {
        "year": year,
        "month": month,
        "month_label": f"{month_name[month]} {year}",
        "products": products,
    }
    return render(request, "dashboards/past_month_performance.html", context)



from .models import CallingList, Prospect

@login_required
def calling_workspace(request, list_id):
    # Get the calling list
    calling_list = get_object_or_404(CallingList, id=list_id)

    # Get prospects assigned to this employee under that list
    prospects = Prospect.objects.filter(
        calling_list=calling_list,
        assigned_to=request.user.employee
    )

    context = {
        "calling_list": calling_list,
        "prospects": prospects,
    }
    return render(request, "calling/callingworkspace.html", context)

import csv
import io
from django.contrib import messages
from .models import CallingList, Prospect

@login_required
def upload_list(request):
    if request.method == "POST" and request.FILES.get("file"):
        import pandas as pd
        from datetime import datetime, timedelta
        from django.utils import timezone
        from .models import CallingList, Prospect, Employee, CalendarEvent

        file = request.FILES["file"]

        # Supports both CSV and Excel
        if file.name.endswith(".csv"):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # ✅ Create calling list
        daily_calls = int(request.POST.get("daily_calls", 5))  # default 5 if not provided
        calling_list = CallingList.objects.create(
            title=request.POST.get("title", "Untitled List"),
            uploaded_by=request.user,   # must be a User
        )

        employees = list(Employee.objects.filter(role="employee"))
        emp_count = len(employees)
        emp_index = 0

        prospects = []

        for _, row in df.iterrows():
            assigned_to = None

            # ✅ if CSV has assigned_to column
            if "assigned_to" in df.columns and pd.notna(row.get("assigned_to")):
                try:
                    assigned_to = Employee.objects.get(user__username=row["assigned_to"])
                except Employee.DoesNotExist:
                    assigned_to = None

            # ✅ if not provided → auto distribute
            if not assigned_to and emp_count > 0:
                assigned_to = employees[emp_index % emp_count]
                emp_index += 1

            p = Prospect.objects.create(
                name=row.get("name", "Unknown"),
                phone=row.get("phone", ""),
                email=row.get("email", ""),
                notes=row.get("notes", ""),
                assigned_to=assigned_to,
                calling_list=calling_list,
            )
            prospects.append(p)

        # ✅ Create calendar events for each employee
        start_date = timezone.now().date()
        # if today is Sat (5) or Sun (6), move to Monday
        if start_date.weekday() in (5, 6):
            start_date += timedelta(days=(7 - start_date.weekday()))

        emp_buckets = {emp.id: [] for emp in employees}
        for p in prospects:
            if p.assigned_to:
                emp_buckets[p.assigned_to.id].append(p)

        for emp_id, plist in emp_buckets.items():
            day_index = 0
            call_index = 0
            current_date = start_date

            for p in plist:
                if call_index >= daily_calls:
                    call_index = 0
                    day_index += 1
                    current_date = start_date + timedelta(days=day_index)

                    # skip weekends
                    while current_date.weekday() in (5, 6):
                        day_index += 1
                        current_date = start_date + timedelta(days=day_index)

                CalendarEvent.objects.create(
                    employee_id=emp_id,
                    title=f"Call: {p.name}",
                    type="call_followup",
                    related_prospect=p,
                    scheduled_time=timezone.make_aware(
                        datetime.combine(current_date, datetime.min.time()) + timedelta(hours=10 + call_index)
                    ),
                    notes=f"Call {p.name}, Phone: {p.phone}",
                )
                call_index += 1

        messages.success(
            request, f"Calling list '{calling_list.title}' uploaded and tasks assigned!"
        )
        return redirect("clients:admin_lists")

    return render(request, "calling/upload_list.html")



@login_required
def admin_lists(request):
    # Fetch all calling lists (latest first)
    calling_lists = CallingList.objects.all().order_by("-created_at")

    context = {
        "calling_lists": calling_lists,
    }
    return render(request, "calling/admin_lists.html", context)

from django.urls import reverse

@login_required
def admin_list_detail(request, list_id):
    calling_list = get_object_or_404(CallingList, id=list_id)
    prospects = calling_list.prospects.select_related("assigned_to").all()
    employees = Employee.objects.select_related("user").all()

    if request.method == "POST":
        prospect_id = request.POST.get("prospect_id")
        employee_id = request.POST.get("employee_id")

        prospect = get_object_or_404(Prospect, id=prospect_id, calling_list=calling_list)

        if employee_id:
            employee = get_object_or_404(Employee, id=employee_id)
            prospect.assigned_to = employee
        else:
            prospect.assigned_to = None  # unassign
        prospect.save()

        return HttpResponseRedirect(reverse("clients:admin_list_detail", args=[list_id]))

    context = {
        "calling_list": calling_list,
        "prospects": prospects,
        "employees": employees,
    }
    return render(request, "calling/admin_list_detail.html", context)

@login_required
def employee_lists(request):
    employee = request.user.employee  
    
    # fetch all lists that have at least one prospect assigned to this employee
    my_lists = CallingList.objects.filter(prospects__assigned_to=employee).distinct()

    # attach a count of how many prospects from each list are assigned to this employee
    for clist in my_lists:
        clist.my_prospects_count = clist.prospects.filter(assigned_to=employee).count()

    context = {
        "my_lists": my_lists
    }
    return render(request, "calling/employee_lists.html", context)


@login_required
def calling_workspace(request, list_id):
    employee = request.user.employee
    calling_list = get_object_or_404(CallingList, id=list_id)

    # only fetch prospects assigned to this employee
    prospects = calling_list.prospects.filter(assigned_to=employee)

    if request.method == "POST":
        action = request.POST.get("action")
        prospect_id = request.POST.get("prospect_id")
        prospect = get_object_or_404(Prospect, id=prospect_id, assigned_to=employee)

        # ---- Log Call Result ----
        if action == "log_call":
            status = request.POST.get("status")
            notes = request.POST.get("notes", "")
            CallRecord.objects.create(
                prospect=prospect,
                employee=employee,
                call_time=timezone.now(),
                status=status,
                notes=notes,
            )
            prospect.status = status
            prospect.save()
            messages.success(request, f"Call logged for {prospect.name}.")

        # ---- Add Follow-up ----
        elif action == "add_followup":
            followup_date = request.POST.get("followup_date")
            notes = request.POST.get("notes", "")
            if followup_date:
                CalendarEvent.objects.create(
                    employee=employee,
                    title=f"Follow-up: {prospect.name}",
                    description=notes,
                    event_date=followup_date,
                    related_prospect=prospect,
                )
                messages.success(request, f"Follow-up added for {prospect.name}.")

        return redirect("clients:callingworkspace", list_id=list_id)

    context = {
        "calling_list": calling_list,
        "prospects": prospects,
    }
    return render(request, "calling/callingworkspace.html", context)

@login_required
def employee_calendar(request):
    employee = request.user.employee
    today = timezone.now().date()

    # fetch all events assigned to this employee
    events = CalendarEvent.objects.filter(employee=employee).order_by("scheduled_time")

    # split into today's events and future events
    todays_events = events.filter(scheduled_time__date=today)
    upcoming_events = events.filter(scheduled_time__date__gt=today)

    context = {
        "todays_events": todays_events,
        "upcoming_events": upcoming_events,
    }
    return render(request, "calendar/employee_calendar.html", context)

# clients/views.py (add imports at top)
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import CalendarEvent, Prospect

# Page view
@login_required
def employee_calendar_page(request):
    """
    Renders the page containing the FullCalendar instance.
    FullCalendar will call the events JSON endpoint to fetch events.
    """
    return render(request, "calendar/employee_calendar.html")


# Events JSON API used by FullCalendar

@require_GET
@login_required
def calendar_events_json(request):
    """
    Returns calendar events as JSON for the currently logged-in employee.
    Compatible with FullCalendar (dayGridMonth, timeGridWeek, timeGridDay).
    """
    employee = request.user.employee

    # Optional: FullCalendar sends `start` and `end` query params
    start = request.GET.get("start")
    end = request.GET.get("end")

    events_qs = CalendarEvent.objects.filter(employee=employee)

    if start:
        try:
            start_dt = parse_datetime(start)
            if start_dt and timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt)
            events_qs = events_qs.filter(scheduled_time__gte=start_dt)
        except Exception:
            pass

    if end:
        try:
            end_dt = parse_datetime(end)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            events_qs = events_qs.filter(scheduled_time__lte=end_dt)
        except Exception:
            pass

    # Build JSON in FullCalendar expected format
    events = []
    for e in events_qs:
        events.append({
            "id": e.id,
            "title": e.title,
            "start": e.scheduled_time.isoformat(),
            # Only include end if you add duration support later
            "extendedProps": {
                "type": e.type,
                "notes": e.notes,
                "related_prospect_id": e.related_prospect.id if e.related_prospect else None,
                "related_prospect_name": e.related_prospect.name if e.related_prospect else None,
            }
        })

    return JsonResponse(events, safe=False)


from django.views.decorators.csrf import csrf_exempt
import json
from django.http import JsonResponse
@login_required
@csrf_exempt
def update_calendar_event(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            event_id = data.get("id")
            start = data.get("start")
            end = data.get("end")

            event = CalendarEvent.objects.get(id=event_id, employee=request.user.employee)
            if start:
                event.scheduled_time = start
            if end:
                event.reminder_time = end  # or use duration if you want
            event.save()

            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)}, status=400)
    return JsonResponse({"success": False}, status=405)

@login_required
def delete_calling_list(request, list_id):
    calling_list = get_object_or_404(CallingList, id=list_id)

    # Only admin should delete
    if request.user.employee.role != "admin":
        messages.error(request, "You are not authorized to delete lists.")
        return redirect("clients:admin_lists")

    calling_list.delete()
    messages.success(request, "Calling list deleted successfully!")
    return redirect("clients:admin_lists")


@login_required
def log_result(request, prospect_id):
    prospect = get_object_or_404(Prospect, id=prospect_id)

    if request.method == "POST":
        status = request.POST.get("status")
        notes = request.POST.get("notes")

        # ✅ Update prospect
        prospect.status = status
        prospect.last_contacted = timezone.now()
        if notes:
            prospect.notes = (prospect.notes or "") + f"\n{timezone.now().strftime('%Y-%m-%d %H:%M')}: {notes}"
        prospect.save()

        # ✅ Auto-create follow-up if status is "follow_up"
        if status == "follow_up":
            CalendarEvent.objects.create(
                employee=request.user.employee,
                title=f"Follow-up: {prospect.name}",
                scheduled_time=timezone.now() + timezone.timedelta(days=1),  # default +1 day
                type="follow_up",
                notes=notes,
                related_prospect=prospect,
            )

        messages.success(request, f"Call result logged for {prospect.name}")
        return redirect("clients:callingworkspace", list_id=prospect.calling_list.id)

    return render(request, "calling/log_result.html", {"prospect": prospect})


from django.utils import timezone

@login_required
def add_followup(request, prospect_id):
    prospect = get_object_or_404(Prospect, id=prospect_id)

    if request.method == "POST":
        followup_date = request.POST.get("scheduled_time")
        notes = request.POST.get("notes")

        if followup_date:
            CalendarEvent.objects.create(
                employee=request.user.employee,
                title=f"Follow-up: {prospect.name}",
                scheduled_time=followup_date,
                type="follow_up",
                notes=notes,
                related_prospect=prospect,
            )
            messages.success(request, f"Follow-up added for {prospect.name}")
            return redirect("clients:callingworkspace", list_id=prospect.calling_list.id)
        else:
            messages.error(request, "Follow-up date is required.")

    return render(request, "calling/add_followup.html", {"prospect": prospect})


from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import CalendarEvent


@login_required
def mark_done(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)
    event.status = "completed"
    event.save()
    messages.success(request, "Event marked as completed ✅")
    return redirect("clients:employee_dashboard")

@login_required
def skip_event(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)
    event.status = "skipped"
    event.save()
    messages.warning(request, "Event skipped ❌")
    return redirect("clients:employee_dashboard")

@login_required
def reschedule_event(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)

    if request.method == "POST":
        new_time = request.POST.get("scheduled_time")
        if new_time:
            event.scheduled_time = new_time
            event.status = "rescheduled"
            event.save()
            messages.success(request, "Event rescheduled 🔄")
            return redirect("clients:employee_dashboard")

    return render(request, "calendar/reschedule_event.html", {"event": event})

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
from django.http import HttpResponse
from django.utils.timezone import now
from django.db.models import Sum
from .models import Sale, Client, Target
from django.contrib.auth.decorators import login_required
from .models import Employee, Sale, Target
from .models import Employee, Sale, Target, MonthlyTargetHistory
import json
from calendar import month_name



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

    context = {
        "total_sales": total_sales,
        "total_points": total_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
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
            return redirect("all_clients")
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
        return redirect("employee_dashboard")  # block non-admins

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
                return redirect("all_sales")  # show latest sales immediately
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

        return redirect("all_clients")

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

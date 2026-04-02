"""Sales views: add, list, approve, edit, delete, incentives, recalculate."""
from datetime import date
from decimal import Decimal
import json
from io import BytesIO
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Q, Sum
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST

from ..models import Client, Sale, Employee, IncentiveRule, IncentiveSlab, Product
from ..forms import AdminSaleForm, EditSaleForm, SaleForm
from .helpers import get_manager_access


def _sale_product_meta():
    products = list(Product.objects.filter(domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH]))
    health = next((p for p in products if p.code == "HEALTH_INS"), None)
    if not health:
        health = next((p for p in products if (p.name or "").strip().lower() == "health insurance"), None)
    insurance_names = [
        p.name
        for p in products
        if p.code in {"HEALTH_INS", "LIFE_INS"}
        or (p.name or "").strip().lower() in {"health insurance", "life insurance"}
    ]
    return {
        "health_product_name": health.name if health else "",
        "insurance_product_names": sorted(set(insurance_names)),
    }


@login_required
def add_sale(request):
    product_meta = _sale_product_meta()
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
                            **product_meta,
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
                            **product_meta,
                        },
                    )

            sale.compute_points()

            if sale.product:
                sale.product_ref = Product.objects.filter(name=sale.product).first()

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
        {"form": form, "employees": employees_qs, "current_employee_id": current_emp_id, **product_meta},
    )


@login_required
def all_sales(request):
    sales_qs = Sale.objects.select_related("client", "employee__user").all().order_by("-date", "-created_at")

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
    policy_type = request.GET.get("policy_type")
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
            | Q(policy_type__icontains=q)
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
    if policy_type in [Sale.POLICY_TYPE_FRESH, Sale.POLICY_TYPE_PORT]:
        sales_qs = sales_qs.filter(policy_type=policy_type)
    if status in [Sale.STATUS_PENDING, Sale.STATUS_APPROVED, Sale.STATUS_REJECTED]:
        sales_qs = sales_qs.filter(status=status)
    if start_date and end_date:
        sales_qs = sales_qs.filter(date__range=[start_date, end_date])

    if not (product or client or employee or policy_type or start_date or end_date or q):
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
        "product_options": Product.objects.filter(domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH]).order_by("display_order", "name"),
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
                if sale.product:
                    sale.product_ref = Product.objects.filter(name=sale.product).first()
                sale.status = Sale.STATUS_APPROVED
                sale.approved_by = request.user
                sale.approved_at = timezone.now()
                sale.rejection_reason = ""
                sale.save()
                messages.success(request, "Sale added successfully!")
                return redirect("clients:all_sales")
    else:
        form = AdminSaleForm()

    return render(request, "sales/admin_add_sale.html", {"form": form, **_sale_product_meta()})


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
    """Full incentive rules builder/modifier page."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        messages.error(request, "You do not have permission to access incentive rules.")
        return redirect("clients:admin_dashboard")

    rules = IncentiveRule.objects.select_related("product_ref").prefetch_related("slabs").all()
    product_options = Product.objects.filter(
        is_active=True,
        archived_at__isnull=True,
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).order_by("display_order", "name")

    return render(
        request,
        "incentives/manage_rules.html",
        {
            "rules": rules,
            "product_options": product_options,
        },
    )


@login_required
@require_POST
def update_incentive_rule(request, rule_id):
    """AJAX: Update unit_amount, points_per_unit, active for a rule."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    rule = get_object_or_404(IncentiveRule, id=rule_id)
    try:
        data = json.loads(request.body)
        product_id = data.get("product_id")
        if product_id not in (None, ""):
            try:
                selected_product = Product.objects.filter(
                    pk=int(product_id),
                    is_active=True,
                    archived_at__isnull=True,
                    domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
                ).first()
            except (TypeError, ValueError):
                selected_product = None
            if not selected_product:
                return JsonResponse({"error": "Invalid product selection."}, status=400)
            duplicate_qs = IncentiveRule.objects.exclude(pk=rule.pk).filter(product_ref=selected_product)
            if duplicate_qs.exists():
                return JsonResponse({"error": f"Rule for '{selected_product.name}' already exists."}, status=400)
            rule.product_ref = selected_product
            rule.product = selected_product.name
        if "unit_amount" in data:
            rule.unit_amount = Decimal(str(data["unit_amount"]))
        if "points_per_unit" in data:
            rule.points_per_unit = Decimal(str(data["points_per_unit"]))
        if "active" in data:
            rule.active = bool(data["active"])
        rule.save()
        label = rule.product_ref.name if rule.product_ref_id else rule.product
        return JsonResponse({"success": True, "message": f"{label} updated."})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def add_incentive_rule(request):
    """AJAX: Add a new incentive rule."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
        product = (data.get("product", "") or "").strip()
        product_id = data.get("product_id")
        unit_amount = Decimal(str(data.get("unit_amount", 0)))
        points_per_unit = Decimal(str(data.get("points_per_unit", 0)))

        selected_product = None
        if product_id not in (None, ""):
            try:
                selected_product = Product.objects.filter(
                    pk=int(product_id),
                    is_active=True,
                    archived_at__isnull=True,
                    domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
                ).first()
            except (TypeError, ValueError):
                selected_product = None

        if not selected_product and product:
            selected_product = Product.objects.filter(name=product).first()

        if selected_product:
            product = selected_product.name

        if not product:
            return JsonResponse({"error": "Product is required."}, status=400)

        existing_qs = IncentiveRule.objects.filter(product=product)
        if selected_product:
            existing_qs = existing_qs | IncentiveRule.objects.filter(product_ref=selected_product)
        if existing_qs.exists():
            return JsonResponse({"error": f"Rule for '{product}' already exists."}, status=400)

        rule = IncentiveRule.objects.create(
            product=product,
            product_ref=selected_product,
            unit_amount=unit_amount,
            points_per_unit=points_per_unit,
            active=True,
        )
        return JsonResponse({
            "success": True,
            "rule": {
                "id": rule.id,
                "product": rule.product,
                "product_id": rule.product_ref_id,
                "unit_amount": str(rule.unit_amount),
                "points_per_unit": str(rule.points_per_unit),
                "active": rule.active,
            },
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def delete_incentive_rule(request, rule_id):
    """AJAX: Delete an incentive rule and all its slabs."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    rule = get_object_or_404(IncentiveRule, id=rule_id)
    product_name = rule.product
    rule.delete()
    return JsonResponse({"success": True, "message": f"Rule for '{product_name}' deleted."})


@login_required
@require_POST
def add_incentive_slab(request, rule_id):
    """AJAX: Add a slab to a rule."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    rule = get_object_or_404(IncentiveRule, id=rule_id)
    try:
        data = json.loads(request.body)
        threshold = Decimal(str(data.get("threshold", 0)))
        payout = Decimal(str(data.get("payout", 0)))
        label = data.get("label", "").strip()

        if threshold <= 0 or payout <= 0:
            return JsonResponse({"error": "Threshold and payout must be positive."}, status=400)

        if IncentiveSlab.objects.filter(rule=rule, threshold=threshold).exists():
            return JsonResponse({"error": f"Slab at ₹{threshold} already exists."}, status=400)

        slab = IncentiveSlab.objects.create(
            rule=rule, threshold=threshold, payout=payout, label=label
        )
        return JsonResponse({
            "success": True,
            "slab": {
                "id": slab.id,
                "threshold": str(slab.threshold),
                "payout": str(slab.payout),
                "label": slab.label,
            },
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def update_incentive_slab(request, slab_id):
    """AJAX: Update an existing slab."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    slab = get_object_or_404(IncentiveSlab, id=slab_id)
    try:
        data = json.loads(request.body)
        if "threshold" in data:
            slab.threshold = Decimal(str(data["threshold"]))
        if "payout" in data:
            slab.payout = Decimal(str(data["payout"]))
        if "label" in data:
            slab.label = data["label"].strip()
        slab.save()
        return JsonResponse({"success": True, "message": "Slab updated."})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def delete_incentive_slab(request, slab_id):
    """AJAX: Delete a slab."""
    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    slab = get_object_or_404(IncentiveSlab, id=slab_id)
    slab.delete()
    return JsonResponse({"success": True, "message": "Slab deleted."})


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
            updated = form.save(commit=False)
            if updated.product:
                updated.product_ref = Product.objects.filter(name=updated.product).first()
            updated.save()
            messages.success(request, "Sale updated successfully!")
            return redirect("clients:all_sales")
    else:
        form = EditSaleForm(instance=sale)

    return render(request, "sales/edit_sale.html", {"form": form, "sale": sale, **_sale_product_meta()})


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


@login_required
def financial_planner(request):
    """Financial planning calculation engine page."""
    return render(request, "sales/financial_planner.html")


def _fmt_money(v):
    try:
        return f"Rs {float(v):,.0f}"
    except (TypeError, ValueError):
        return "Rs 0"


def _fmt_pct(v):
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _safe_name(raw_name):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (raw_name or "Client").strip())
    return cleaned[:40] or "Client"


@login_required
@require_POST
def financial_planner_download_report(request):
    """Generate professional PDF report for Financial Planner with firm branding."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid report payload."}, status=400)

    planner = payload.get("planner") or {}
    if not planner:
        return JsonResponse({"error": "Planner data is required."}, status=400)

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, inch
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, Image, Frame, PageTemplate, KeepTogether
        )
        from reportlab.pdfgen import canvas
    except ImportError:
        return JsonResponse(
            {"error": "PDF dependency missing. Install reportlab first."},
            status=500,
        )

    # Get firm settings
    from ..models import FirmSettings
    firm = FirmSettings.get_settings()
    
    client_name = planner.get("client_name") or "Client"
    today_str = timezone.localdate().strftime("%Y%m%d")
    filename = f"financial_plan_{_safe_name(client_name)}_{today_str}.pdf"

    buffer = BytesIO()
    
    # Custom page template with header and footer
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_page_number(num_pages)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def draw_page_number(self, page_count):
            self.setFont("Helvetica", 9)
            self.setFillColor(colors.grey)
            page_num = f"Page {self._pageNumber} of {page_count}"
            self.drawRightString(A4[0] - 1.5*cm, 1*cm, page_num)
            
            # Footer with firm name
            if firm.firm_name:
                self.setFont("Helvetica-Oblique", 8)
                self.setFillColor(colors.HexColor("#888888"))
                footer_text = f"{firm.firm_name}"
                if firm.email or firm.phone:
                    footer_parts = []
                    if firm.email:
                        footer_parts.append(firm.email)
                    if firm.phone:
                        footer_parts.append(firm.phone)
                    footer_text += f" | {' | '.join(footer_parts)}"
                self.drawCentredString(A4[0]/2, 1*cm, footer_text)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2*cm,
        rightMargin=2*cm,
        topMargin=2.5*cm,
        bottomMargin=2.5*cm,
    )

    # Custom styles
    styles = getSampleStyleSheet()
    
    # Parse primary color from firm settings
    try:
        primary_color = colors.HexColor(firm.primary_color)
    except:
        primary_color = colors.HexColor("#E5B740")
    
    # Create custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=primary_color,
        spaceAfter=6,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor("#555555"),
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName='Helvetica'
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=primary_color,
        spaceAfter=12,
        spaceBefore=16,
        fontName='Helvetica-Bold',
        borderWidth=0,
        borderColor=primary_color,
        borderPadding=5,
        leftIndent=0,
    )
    
    subheading_style = ParagraphStyle(
        'CustomSubheading',
        parent=styles['Heading3'],
        fontSize=11,
        textColor=colors.HexColor("#444444"),
        spaceAfter=8,
        spaceBefore=10,
        fontName='Helvetica-Bold'
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor("#333333"),
        spaceAfter=6,
        fontName='Helvetica'
    )
    
    highlight_style = ParagraphStyle(
        'Highlight',
        parent=styles['Normal'],
        fontSize=11,
        textColor=primary_color,
        spaceAfter=8,
        fontName='Helvetica-Bold'
    )

    elements = []
    
    # ===== HEADER SECTION =====
    # Add logo if available
    if firm.logo and hasattr(firm.logo, 'path'):
        try:
            import os
            if os.path.exists(firm.logo.path):
                logo_img = Image(firm.logo.path, width=4*cm, height=1.5*cm, kind='proportional')
                logo_img.hAlign = 'CENTER'
                elements.append(logo_img)
                elements.append(Spacer(1, 0.3*cm))
        except:
            pass  # Skip logo if there's any issue
    
    # Always show firm name for clear branding, even when logo is present.
    firm_name_para = Paragraph(f"<b>{firm.firm_name}</b>", title_style)
    elements.append(firm_name_para)
    
    # Firm details
    firm_details = []
    if firm.address:
        firm_details.append(firm.address.replace('\n', ', '))
    contact_parts = []
    if firm.phone:
        contact_parts.append(f"Tel: {firm.phone}")
    if firm.email:
        contact_parts.append(f"Email: {firm.email}")
    if firm.website:
        contact_parts.append(f"Web: {firm.website}")
    if contact_parts:
        firm_details.append(" | ".join(contact_parts))
    
    if firm_details:
        for detail in firm_details:
            elements.append(Paragraph(detail, subtitle_style))
    
    elements.append(Spacer(1, 0.5*cm))
    
    # Horizontal line
    line_data = [['', '']]
    line_table = Table(line_data, colWidths=[17*cm])
    line_table.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 2, primary_color),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.HexColor("#CCCCCC")),
    ]))
    elements.append(line_table)
    elements.append(Spacer(1, 0.5*cm))
    
    # Report title
    elements.append(Paragraph("Financial Planning Report", heading_style))
    
    # Client info box
    today = timezone.localdate().strftime("%d %B %Y")
    client_info_data = [
        [Paragraph("<b>Client Name:</b>", normal_style), Paragraph(client_name, normal_style)],
        [Paragraph("<b>Report Date:</b>", normal_style), Paragraph(today, normal_style)],
    ]
    client_info_table = Table(client_info_data, colWidths=[4*cm, 13*cm])
    client_info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F9F9F9")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#DDDDDD")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(client_info_table)
    elements.append(Spacer(1, 0.7*cm))

    # ===== INPUTS SECTION =====
    inputs = planner.get("inputs") or {}
    elements.append(Paragraph("1. Client Inputs & Assumptions", heading_style))
    
    input_rows = [
        [Paragraph("<b>Parameter</b>", normal_style), Paragraph("<b>Value</b>", normal_style)],
        ["Current Age", str(inputs.get("age", "-"))],
        ["Retirement Age", str(inputs.get("retire_age", "-"))],
        ["Life Expectancy", str(inputs.get("life_expectancy", "-"))],
        ["Annual Income", _fmt_money(inputs.get("income", 0))],
        ["Annual Expense", _fmt_money(inputs.get("expense", 0))],
        ["Income Growth Rate", _fmt_pct(inputs.get("income_growth_pct", 0))],
        ["Expected Return Rate", _fmt_pct(inputs.get("return_pct", 0))],
        ["General Inflation", _fmt_pct(inputs.get("inflation_pct", 0))],
        ["Expense Inflation", _fmt_pct(inputs.get("expense_inflation_pct", 0))],
    ]
    
    input_table = Table(input_rows, colWidths=[9 * cm, 8 * cm])
    input_table.setStyle(
        TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor("#F5F5F5")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
        ])
    )
    elements.append(input_table)
    elements.append(Spacer(1, 0.6*cm))

    # ===== RETIREMENT SECTION =====
    retirement = planner.get("retirement") or {}
    elements.append(Paragraph("2. Retirement Analysis", heading_style))
    
    retirement_rows = [
        [Paragraph("<b>Metric</b>", normal_style), Paragraph("<b>Value</b>", normal_style)],
        ["Years to Retirement", str(retirement.get("years_to_retirement", "-"))],
        ["Years in Retirement", str(retirement.get("years_in_retirement", "-"))],
        ["Real Rate of Return", _fmt_pct(retirement.get("real_rate_pct", 0))],
        ["Future Annual Expense (at retirement)", _fmt_money(retirement.get("future_expense", 0))],
        [Paragraph("<b>Retirement Corpus Required</b>", highlight_style), 
         Paragraph(f"<b>{_fmt_money(retirement.get('corpus', 0))}</b>", highlight_style)],
        [Paragraph("<b>Monthly SIP for Retirement</b>", highlight_style), 
         Paragraph(f"<b>{_fmt_money(retirement.get('retirement_sip', 0))}</b>", highlight_style)],
    ]
    
    retirement_table = Table(retirement_rows, colWidths=[9 * cm, 8 * cm])
    retirement_table.setStyle(
        TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor("#F5F5F5")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
        ])
    )
    elements.append(retirement_table)
    elements.append(Spacer(1, 0.6*cm))

    # ===== POST-RETIREMENT WITHDRAWAL PREVIEW =====
    withdrawal_rows = planner.get("withdrawal_preview") or []
    if withdrawal_rows:
        elements.append(Paragraph("Post-Retirement Withdrawal Projection", subheading_style))
        elements.append(Paragraph(
            "This table shows how your retirement corpus will be drawn down during retirement years:",
            normal_style
        ))
        elements.append(Spacer(1, 0.3*cm))
        
        w_data = [[Paragraph("<b>Year</b>", normal_style), 
                   Paragraph("<b>Age</b>", normal_style), 
                   Paragraph("<b>Withdrawal</b>", normal_style), 
                   Paragraph("<b>Start Balance</b>", normal_style), 
                   Paragraph("<b>End Balance</b>", normal_style)]]
        
        for r in withdrawal_rows[:10]:  # Limit to first 10 years for readability
            w_data.append([
                str(r.get("year", "-")),
                str(r.get("age", "-")),
                _fmt_money(r.get("withdrawal", 0)),
                _fmt_money(r.get("portfolio_start", 0)),
                _fmt_money(r.get("portfolio_end", 0)),
            ])
        
        w_table = Table(w_data, repeatRows=1, colWidths=[2*cm, 2*cm, 4*cm, 4.5*cm, 4.5*cm])
        w_table.setStyle(
            TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ])
        )
        elements.append(w_table)
        if len(withdrawal_rows) > 10:
            elements.append(Paragraph(
                f"<i>Note: Showing first 10 years of {len(withdrawal_rows)} total years in retirement</i>",
                normal_style
            ))
        elements.append(Spacer(1, 0.6*cm))

    # ===== GOALS & FUTURE PLAN =====
    goals = planner.get("goals") or []
    summary = planner.get("summary") or {}
    
    elements.append(Paragraph("3. Financial Goals & Requirements", heading_style))
    elements.append(Paragraph(
        f"<b>Total Monthly SIP Required: {_fmt_money(summary.get('total_sip', 0))}</b>",
        highlight_style
    ))
    elements.append(Paragraph(
        f"Goal Component: {_fmt_money(summary.get('goal_sip', 0))}/month | "
        f"Retirement Component: {_fmt_money(summary.get('retirement_sip', 0))}/month",
        normal_style
    ))
    elements.append(Spacer(1, 0.3*cm))
    
    if goals:
        g_data = [[Paragraph("<b>Goal</b>", normal_style), 
                   Paragraph("<b>Years</b>", normal_style), 
                   Paragraph("<b>Future Cost</b>", normal_style), 
                   Paragraph("<b>Monthly SIP</b>", normal_style)]]
        
        for g in goals:
            g_data.append([
                g.get("name") or "Unnamed Goal",
                str(g.get("years_to_goal", "-")),
                _fmt_money(g.get("future_cost", 0)),
                _fmt_money(g.get("sip", 0)),
            ])
        
        g_table = Table(g_data, repeatRows=1, colWidths=[5*cm, 2.5*cm, 4.5*cm, 5*cm])
        g_table.setStyle(
            TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ])
        )
        elements.append(g_table)
    else:
        elements.append(Paragraph("<i>No specific goals have been added to this plan.</i>", normal_style))
    
    elements.append(Spacer(1, 0.6*cm))

    # ===== ASSETS SECTION =====
    assets = planner.get("assets") or []
    elements.append(Paragraph("4. Current Assets", heading_style))
    elements.append(
        Paragraph(
            f"<b>Current Total:</b> {_fmt_money(summary.get('current_assets', 0))} | "
            f"<b>Projected at Retirement:</b> {_fmt_money(summary.get('projected_assets', 0))}",
            normal_style,
        )
    )
    elements.append(Spacer(1, 0.3*cm))
    
    if assets:
        a_data = [[Paragraph("<b>Asset</b>", normal_style), 
                   Paragraph("<b>Category</b>", normal_style), 
                   Paragraph("<b>Current Value</b>", normal_style), 
                   Paragraph("<b>Return</b>", normal_style), 
                   Paragraph("<b>Projected Value</b>", normal_style)]]
        for a in assets:
            a_data.append([
                a.get("name") or "Unnamed Asset",
                a.get("category") or "-",
                _fmt_money(a.get("current_value", 0)),
                _fmt_pct(a.get("return_pct", 0)),
                _fmt_money(a.get("projected_value", 0)),
            ])
        a_table = Table(a_data, repeatRows=1, colWidths=[4*cm, 3*cm, 3.5*cm, 2.5*cm, 4*cm])
        a_table.setStyle(
            TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('ALIGN', (2, 1), (-1, -1), 'CENTER'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ])
        )
        elements.append(a_table)
    else:
        elements.append(Paragraph("<i>No assets have been added to this plan.</i>", normal_style))
    
    elements.append(Spacer(1, 0.6*cm))

    # ===== SUMMARY SECTION =====
    elements.append(Paragraph("5. Financial Summary", heading_style))
    summary_rows = [
        [Paragraph("<b>Metric</b>", normal_style), Paragraph("<b>Amount</b>", normal_style)],
        ["Retirement Corpus Required", _fmt_money(summary.get("corpus", 0))],
        ["Monthly SIP for Retirement", _fmt_money(summary.get("retirement_sip", 0))],
        ["Monthly SIP for Goals", _fmt_money(summary.get("goal_sip", 0))],
        [Paragraph("<b>Total Monthly SIP Required</b>", highlight_style), 
         Paragraph(f"<b>{_fmt_money(summary.get('total_sip', 0))}</b>", highlight_style)],
        ["Net Uncovered Corpus", _fmt_money(summary.get("net_uncovered_corpus", 0))],
    ]
    s_table = Table(summary_rows, colWidths=[9 * cm, 8 * cm])
    s_table.setStyle(
        TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BACKGROUND', (0, 1), (0, -1), colors.HexColor("#F5F5F5")),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
        ])
    )
    elements.append(s_table)
    elements.append(Spacer(1, 0.6*cm))

    # ===== STRESS TEST SECTION =====
    stress = planner.get("stress_test") or {}
    grid_rows = stress.get("grid") or []
    if grid_rows:
        elements.append(Paragraph("6. Scenario Analysis (Stress Test)", heading_style))
        elements.append(Paragraph(
            "This table shows the required monthly SIP under various combinations of return and inflation rates:",
            normal_style
        ))
        elements.append(Spacer(1, 0.3*cm))
        
        st_header = [Paragraph("<b>Inflation / Return</b>", normal_style)] + \
                    [Paragraph(f"<b>{v}%</b>", normal_style) for v in (stress.get("returns") or [])]
        st_data = [st_header]
        for row in grid_rows:
            row_data = [Paragraph(f"<b>{row.get('inflation', '-')}%</b>", normal_style)]
            for cell in row.get("cells") or []:
                sip_val = _fmt_money(cell.get("sip", 0))
                row_data.append(sip_val)
            st_data.append(row_data)
        
        st_table = Table(st_data, repeatRows=1)
        st_table.setStyle(
            TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), primary_color),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 1), (0, -1), colors.HexColor("#F5F5F5")),
                ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
                ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ])
        )
        elements.append(st_table)

        custom = stress.get("custom") or {}
        if custom:
            elements.append(Spacer(1, 0.3*cm))
            elements.append(
                Paragraph(
                    f"<b>Custom Scenario:</b> Return {_fmt_pct(custom.get('return_pct', 0))}, "
                    f"Inflation {_fmt_pct(custom.get('inflation_pct', 0))} → "
                    f"Corpus: {_fmt_money(custom.get('corpus', 0))}, "
                    f"Monthly SIP: {_fmt_money(custom.get('sip', 0))}",
                    normal_style,
                )
            )
        elements.append(Spacer(1, 0.6*cm))

    # Add disclaimer or notes section
    elements.append(Paragraph("Important Notes", subheading_style))
    elements.append(Paragraph(
        "- This financial plan is based on the assumptions and inputs provided above.",
        normal_style
    ))
    elements.append(Paragraph(
        "- Actual returns may vary and are subject to market conditions.",
        normal_style
    ))
    elements.append(Paragraph(
        "- Please review this plan with your financial advisor before making investment decisions.",
        normal_style
    ))
    elements.append(Paragraph(
        f"- This report was generated on {timezone.localdate().strftime('%d %B %Y')} for informational purposes only.",
        normal_style
    ))

    # Build PDF with custom canvas for page numbers
    doc.build(elements, canvasmaker=NumberedCanvas)

    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

"""Sales views: add, list, approve, edit, delete, incentives, recalculate."""
import logging
from calendar import month_name, monthrange
from datetime import date
from decimal import Decimal
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST

from .. import permissions
from ..models import (BonusPayout, Client, Sale, Employee, IncentiveRule,
                      IncentiveSlab, Product)
from ..forms import AdminSaleForm, EditSaleForm
from ..services import incentives as incentives_service
from ..services import sales as sales_service
from ..templatetags.custom_filters import inr
from .helpers import get_manager_access, parse_date_param, success_with_drive_link, name_words_q

logger = logging.getLogger(__name__)


def _sale_product_meta(show_margin=False):
    # Includes sub-products: is_health / is_insurance are parent-aware, so a
    # child of Health/Life reports the same and toggles the same form fields.
    # Margin (FYC) figures are ADMIN-ONLY: they're only put on the page (and only
    # rendered) when show_margin is true, so a non-admin sale page never even
    # ships the rates in its source.
    from ..forms import _product_children_map, _ppt_options_map
    from ..models import FirmSettings, PlanPptRate
    products = list(
        Product.objects.filter(domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH])
        .select_related("parent")
    )
    meta = {
        "health_product_names": sorted({p.name for p in products if p.is_health}),
        "insurance_product_names": sorted({p.name for p in products if p.is_insurance}),
        "product_children": _product_children_map(),
        "ppt_options": _ppt_options_map(),
        "show_margin": show_margin,
    }
    if show_margin:
        mdrt = FirmSettings.get_settings().is_mdrt_active()
        # FYC per (plan name, ppt) for the currently-active designation.
        desig = PlanPptRate.DESIG_MDRT if mdrt else PlanPptRate.DESIG_ADVISOR
        fyc_by_plan = {}
        for r in PlanPptRate.objects.filter(designation=desig).exclude(fyc__isnull=True).select_related("product"):
            fyc_by_plan.setdefault(r.product.name, {})[r.ppt] = str(r.fyc)
        meta["ppt_fyc"] = fyc_by_plan
        meta["mdrt_active"] = mdrt
    return meta


def _duplicate_confirm(request, sale):
    """Render the "this sale is already on the books" confirmation, or None.

    Shared by both web sale forms. The posted fields are carried across as
    hidden inputs so confirming re-submits exactly what was typed — no form
    template has to be able to rebuild its own state (the client picker
    couldn't).
    """
    if request.POST.get("confirm_duplicate"):
        return None
    dup = sales_service.find_duplicate(sale)
    if dup is None:
        return None
    carried = [(k, v) for k, vs in request.POST.lists() for v in vs
               if k not in ("csrfmiddlewaretoken", "confirm_duplicate")]
    return render(request, "sales/confirm_duplicate.html", {
        "crumbs": [{"label": "Sales", "url": reverse("clients:all_sales")},
                   {"label": "Duplicate check"}],
        "duplicate": dup,
        "sale": sale,
        "window_days": sales_service.DUPLICATE_WINDOW_DAYS,
        "post_data": carried,
        "action": request.path,
    })


@login_required
def add_sale(request):
    is_admin_user = permissions.is_admin(request.user)
    product_meta = _sale_product_meta(show_margin=is_admin_user)
    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)

            # Anyone may attribute a sale to a colleague — a sale is often
            # entered by whoever is at a desk. A non-admin's sale still goes to
            # pending below, so crediting someone else is not self-approval.
            sale.employee = (form.cleaned_data.get("employee")
                             or getattr(request.user, "employee", None))

            if sale.employee is None:
                messages.error(request, "Your account is not mapped to an employee. Contact an administrator.")
                return render(
                    request,
                    "sales/add_sale.html",
                    {
                        "form": form,
                        "employees": Employee.objects.filter(active=True).select_related("user"),
                        "current_employee_id": None,
                        **product_meta,
                    },
                )

            if not sale.client:
                client_id = request.POST.get("client")
                if not client_id:
                    messages.error(request, "Please select a client from search results.")
                    return render(
                        request,
                        "sales/add_sale.html",
                        {
                            "form": form,
                            "employees": Employee.objects.filter(active=True).select_related("user"),
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
                            "employees": Employee.objects.filter(active=True).select_related("user"),
                            "current_employee_id": getattr(request.user, "employee").id
                            if hasattr(request.user, "employee")
                            else None,
                                **product_meta,
                        },
                    )

            # Same client + product + amount within two months = almost always
            # the same sale entered twice. Ask once; a confirmed one goes in.
            confirm = _duplicate_confirm(request, sale)
            if confirm is not None:
                return confirm

            sales_service.finalize_new_sale(sale, request.user, auto_approve=is_admin_user)
            success_with_drive_link(request, "Sale added.", sale.client,
                                    insurance=sale.is_insurance)
            return redirect("clients:all_sales")
    else:
        initial = {}
        if hasattr(request.user, "employee"):
            initial["employee"] = request.user.employee.id
            initial["date"] = date.today()
        form = AdminSaleForm(initial=initial)

    employees_qs = Employee.objects.filter(active=True).select_related("user")
    current_emp_id = (
        getattr(request.user, "employee").id if hasattr(request.user, "employee") else None
    )
    return render(
        request,
        "sales/add_sale.html",
        {
            "form": form,
            "employees": employees_qs,
            "current_employee_id": current_emp_id,
            **product_meta,
        },
    )


@login_required
def all_sales(request):
    sales_qs = Sale.objects.select_related("client", "employee__user").all().order_by("-date", "-created_at")

    user_emp = getattr(request.user, "employee", None)
    is_manager = bool(user_emp and user_emp.role == "manager")
    manager_access = get_manager_access() if is_manager else None

    own_only = (
        (hasattr(request.user, "employee") and request.user.employee.role == "employee")
        or (is_manager and not permissions.can(request.user, "view_all_sales"))
    )
    if own_only:
        sales_qs = sales_qs.filter(employee=request.user.employee)

    product = request.GET.get("product")
    client = request.GET.get("client")
    employee = request.GET.get("employee")
    policy_type = request.GET.get("policy_type")
    status = request.GET.get("status")
    start_date = parse_date_param(request.GET.get("start_date"))
    end_date = parse_date_param(request.GET.get("end_date"))
    q = (request.GET.get("q") or "").strip()
    focus = (request.GET.get("focus") or "").strip()

    # Nothing asked for → this month to date. The page used to default to
    # today's sales, so it opened blank on any day nobody had booked yet.
    default_range = not (product or client or employee or policy_type or status
                         or start_date or end_date or q or focus)
    if default_range:
        today_ = timezone.localdate()
        start_date, end_date = today_.replace(day=1), today_

    if q:
        sales_qs = sales_qs.filter(
            name_words_q("client__name", q)
            | Q(client__email__icontains=q)
            | Q(client__phone__icontains=q)
            | Q(employee__user__username__icontains=q)
            | Q(employee__user__first_name__icontains=q)
            | Q(employee__user__last_name__icontains=q)
            | Q(product__icontains=q)
            | Q(policy_type__icontains=q)
        )

    if product:
        # Filter dropdowns only offer main products; a sale is stored under its
        # sub-product name, so match the chosen main product AND its children.
        child_names = list(Product.objects.filter(parent__name=product).values_list("name", flat=True))
        sales_qs = sales_qs.filter(product__in=[product] + child_names)
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
    # Each bound stands alone — "from date" without an end date must still filter.
    if start_date:
        sales_qs = sales_qs.filter(date__gte=start_date)
    if end_date:
        sales_qs = sales_qs.filter(date__lte=end_date)

    # "My day" links land here. The counts on the dashboard come from the same
    # selectors, so the list always matches the number that was clicked.
    focus_label = ""
    if focus in ("renewals7", "emi"):
        scope = None if permissions.is_admin(request.user) else user_emp
        today_ = timezone.localdate()
        if focus == "renewals7":
            ids = sales_service.renewal_due_sale_ids(today_, employee=scope)
            focus_label = "Policies renewing within 7 days"
        else:
            ids = sales_service.emi_due_sale_ids(today_, employee=scope)
            focus_label = "EMI instalments due this month"
        sales_qs = sales_qs.filter(pk__in=ids)

    paginator = Paginator(sales_qs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    qdict = request.GET.copy()
    qdict.pop("page", None)
    qstring = qdict.urlencode()

    # Status counts respect the same visibility scope AND the same dates as the
    # list itself — a tile that counts all time above a one-month list is a lie.
    scope = Sale.objects.all()
    if own_only:
        scope = scope.filter(employee=request.user.employee)
    if start_date:
        scope = scope.filter(date__gte=start_date)
    if end_date:
        scope = scope.filter(date__lte=end_date)
    agg = scope.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Sale.STATUS_PENDING)),
        approved=Count("id", filter=Q(status=Sale.STATUS_APPROVED)),
        rejected=Count("id", filter=Q(status=Sale.STATUS_REJECTED)),
        amount=Sum("amount", filter=Q(status=Sale.STATUS_APPROVED)),
    )
    base = reverse("clients:all_sales")
    # Tiles keep the period in view — clicking "Pending" narrows the same dates.
    period = ""
    if start_date:
        period += f"&start_date={start_date.isoformat()}"
    if end_date:
        period += f"&end_date={end_date.isoformat()}"
    context = {
        "crumbs": [{"label": "Sales"}] + ([{"label": focus_label}] if focus_label else []),
        "focus_label": focus_label,
        "start_date": start_date,
        "end_date": end_date,
        "default_range": default_range,
        "kpis": [
            {"label": "All Sales", "value": agg["total"], "color": "#4338CA",
             "url": f"{base}?{period.lstrip('&')}", "active": not status},
            {"label": "Pending", "value": agg["pending"], "color": "#B45309",
             "url": f"{base}?status={Sale.STATUS_PENDING}{period}", "active": status == Sale.STATUS_PENDING},
            {"label": "Approved", "value": agg["approved"], "color": "#15803D",
             "url": f"{base}?status={Sale.STATUS_APPROVED}{period}", "active": status == Sale.STATUS_APPROVED},
            {"label": "Rejected", "value": agg["rejected"], "color": "#BE123C",
             "url": f"{base}?status={Sale.STATUS_REJECTED}{period}", "active": status == Sale.STATUS_REJECTED},
            {"label": "Approved Value", "value": f"₹{inr(agg['amount'] or 0)}", "color": "#0F766E"},
        ],
        "sales": page_obj,
        "is_employee": hasattr(request.user, "employee") and request.user.employee.role == "employee",
        "is_manager": is_manager,
        "manager_can_edit": bool(is_manager and permissions.can(request.user, "edit_sales")),
        "qstring": qstring,
        "q": q,
        "status": status,
        "product_options": Product.objects.filter(parent__isnull=True, domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH]).order_by("display_order", "name"),
    }
    return render(request, "sales/all_sales.html", context)


@login_required
def admin_add_sale(request):
    user_emp = getattr(request.user, "employee", None)
    if not permissions.is_admin(request.user):
        return redirect("clients:employee_dashboard")

    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            if not sale.employee_id:
                form.add_error("employee", "Please select an employee for this sale.")
            else:
                confirm = _duplicate_confirm(request, sale)
                if confirm is not None:
                    return confirm
                sales_service.finalize_new_sale(sale, request.user, auto_approve=True)
                success_with_drive_link(request, "Sale added.", sale.client,
                                        insurance=sale.is_insurance)
                return redirect("clients:all_sales")
    else:
        form = AdminSaleForm()

    return render(request, "sales/admin_add_sale.html", {"form": form, **_sale_product_meta(show_margin=True)})


@login_required
def approve_sales(request):
    user_emp = getattr(request.user, "employee", None)
    is_admin = permissions.is_admin(request.user)
    is_manager = bool(user_emp and user_emp.role == "manager")
    manager_access = get_manager_access() if is_manager else None

    if not permissions.can(request.user, "approve_sales"):
        return HttpResponseForbidden("You do not have permission to approve sales.")

    if request.method == "POST":
        action = request.POST.get("action")
        sale_id = request.POST.get("sale_id")
        reason = (request.POST.get("reason") or "").strip()
        sale = get_object_or_404(Sale, id=sale_id)
        if action == "approve":
            sales_service.approve_sale(sale, request.user)
            messages.success(request, f"Approved sale #{sale.id}.")
        elif action == "reject":
            sales_service.reject_sale(sale, request.user, reason)
            messages.info(request, f"Rejected sale #{sale.id}.")
        return redirect("clients:approve_sales")

    employee_filter = request.GET.get("employee", "").strip()
    start_date = parse_date_param(request.GET.get("start_date"))
    end_date = parse_date_param(request.GET.get("end_date"))

    sales_qs = Sale.objects.filter(status=Sale.STATUS_PENDING).select_related(
        "client", "employee__user", "product_ref"
    )
    if is_manager and not permissions.can(request.user, "view_all_sales"):
        sales_qs = sales_qs.filter(employee=user_emp)
    if employee_filter:
        sales_qs = sales_qs.filter(
            Q(employee__user__username__icontains=employee_filter)
            | Q(employee__user__first_name__icontains=employee_filter)
            | Q(employee__user__last_name__icontains=employee_filter)
        )
    if start_date:
        sales_qs = sales_qs.filter(date__gte=start_date)
    if end_date:
        sales_qs = sales_qs.filter(date__lte=end_date)

    sales = list(sales_qs.order_by("-date", "-created_at"))

    context = {
        "sales": sales,
        "employee_filter": employee_filter,
        "start_date": start_date,
        "end_date": end_date,
    }
    return render(request, "sales/approve_sales.html", context)


@login_required
def manage_incentive_rules(request):
    """Full incentive rules builder/modifier page."""
    if not permissions.can(request.user, "manage_incentives"):
        messages.error(request, "You do not have permission to access incentive rules.")
        return redirect("clients:admin_dashboard")

    rules = IncentiveRule.objects.select_related("product_ref").prefetch_related("slabs").all()
    # One plain sentence per rule, derived from the rule itself, so the screen
    # says what a rule does instead of leaving it to be read off two raw fields.
    for r in rules:
        r.base_percent = incentives_service.unit_rate_percent(r)
        r.is_yearly = r.slab_period == IncentiveRule.PERIOD_FY
        r.is_rate = r.slab_mode == IncentiveRule.MODE_RATE and r.slabs.exists()
        r.window_word = "year (April–March)" if r.is_yearly else "month"
        if r.is_rate:
            r.summary = (
                f"The seller's own {r.window_word} total picks a rate from the table "
                f"below, and the whole {r.window_word.split(' ')[0]} is paid at that rate."
            )
        elif r.slabs.exists():
            r.summary = (
                f"{r.base_percent:.2f}% of every sale, plus one prize decided by the "
                f"seller's {r.window_word} total. The prize is the level reached, paid "
                f"once — climbing releases only the difference."
            )
        else:
            r.summary = f"{r.base_percent:.2f}% of every sale. No levels, no targets."
    # Rules are set per main product — `rule_sale_q` already folds a
    # sub-product's sales into its category's rule.
    product_options = Product.objects.selectable().main().filter(
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).in_display_order()

    return render(
        request,
        "incentives/manage_rules.html",
        {
            "kpis": [
                {"label": "Incentive Rules", "value": len(rules), "color": "#4338CA"},
                {"label": "Active",
                 "value": sum(1 for r in rules if getattr(r, "is_active", True)),
                 "color": "#15803D"},
                {"label": "Total Slabs", "value": sum(r.slabs.count() for r in rules),
                 "color": "#B45309"},
            ],
            "rules": rules,
            "product_options": product_options,
        },
    )


@login_required
def life_bonus_tracker(request):
    """Every employee's position on the life ladder for one financial year.

    The prize is the rung the year's own volume reaches — nothing is deducted
    for a big month.
    """
    if not permissions.is_admin_or_manager(request.user):
        messages.error(request, "You do not have permission to view life bonus status.")
        return redirect("clients:admin_dashboard")

    today = timezone.localdate()
    try:
        fy = int(request.GET.get("fy", incentives_service.fy_start_year(today)))
    except (TypeError, ValueError):
        fy = incentives_service.fy_start_year(today)

    rule = (IncentiveRule.objects.filter(
        active=True, slab_mode=IncentiveRule.MODE_BONUS, slabs__isnull=False)
        .prefetch_related("slabs").distinct().first())
    if rule is None:
        messages.error(request, "No ladder-based incentive rule is configured.")
        return redirect("clients:incentive_structure")

    rows = []
    totals = {"volume": Decimal("0"), "base": Decimal("0"), "level": Decimal("0"),
              "released": Decimal("0"), "paid": Decimal("0"), "shortfall": Decimal("0")}
    for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username"):
        st = incentives_service.life_bonus_status(rule, e, fy)
        if not st["volume"]:
            continue
        for k in ("volume", "base", "level", "released"):
            totals[k] += st[k]
        totals["paid"] += st["paid_manually"]
        totals["shortfall"] += st["shortfall"]
        rows.append(st)

    expanded = request.GET.get("employee")
    detail = next((r for r in rows if str(r["employee"].id) == expanded), None)

    return render(request, "incentives/life_bonus.html", {
        "crumbs": [{"label": "Admin", "url": reverse("clients:admin_dashboard")},
                   {"label": "Life Bonus Status"}],
        "kpis": [
            {"label": "Life sold this FY", "value": f"₹{inr(totals['volume'])}", "color": "#4338CA"},
            {"label": "Prize handed over", "value": f"₹{inr(totals['released'])}", "color": "#B45309"},
            {"label": "Still owed", "value": f"₹{inr(totals['shortfall'])}", "color": "#BE123C",
             "sub": "payable from 1 Apr " + str(fy + 1)},
            {"label": "Base paid", "value": f"₹{inr(totals['base'])}", "color": "#15803D"},
        ],
        "rule": rule, "rows": rows, "totals": totals, "detail": detail,
        "fy": fy, "fy_label": f"{fy}–{fy + 1}",
        "fy_closed": today >= date(fy + 1, 4, 1),
        "fy_options": list(range(incentives_service.fy_start_year(today), incentives_service.fy_start_year(today) - 4, -1)),
        "ladder": incentives_service.ladder(rule),
    })


@login_required
@require_POST
def record_bonus_payout(request):
    """Log a fixed monthly bonus paid by hand, so the ladder nets it off.

    Idempotent per (employee, rule, month) — re-submitting a month overwrites
    the figure rather than stacking a second payment.
    """
    if not permissions.can(request.user, "manage_incentives"):
        messages.error(request, "You do not have permission to record bonus payouts.")
        return redirect("clients:admin_dashboard")

    employee = get_object_or_404(Employee, pk=request.POST.get("employee"))
    rule = get_object_or_404(IncentiveRule, pk=request.POST.get("rule"))
    amount = _dec_param(request.POST.get("amount"))
    try:
        for_month = date.fromisoformat(request.POST.get("for_month", "")).replace(day=1)
    except ValueError:
        messages.error(request, "Pick the month this payout was for.")
        return redirect("clients:life_bonus_tracker")

    back = f"{reverse('clients:life_bonus_tracker')}?fy={request.POST.get('fy', '')}&employee={employee.id}"
    if amount <= 0:
        BonusPayout.objects.filter(employee=employee, rule=rule, for_month=for_month).delete()
        messages.success(request, f"Cleared the {for_month:%b %Y} payout for {employee}.")
        return redirect(back)

    BonusPayout.objects.update_or_create(
        employee=employee, rule=rule, for_month=for_month,
        defaults={"amount": amount, "created_by": request.user},
    )
    # The ladder reads this when pricing, so anything already booked in the
    # window has to be re-run or the deduction only applies to future sales.
    for sale in Sale.objects.filter(
            employee=employee, status=Sale.STATUS_APPROVED,
            date__range=incentives_service.period_bounds(rule, for_month)).order_by("date", "id"):
        sale.save()
    messages.success(
        request,
        f"Recorded ₹{inr(amount)} for {for_month:%b %Y}. The yearly ladder now nets it off.")
    return redirect(back)


@login_required
def incentive_payout(request):
    """The month's incentive bill, and where each employee stands on the ladder.

    Answers the four things you cannot get from a sales list: what to pay out
    this month, how much of that was ladder bonus, which day each bonus was
    released, and how much prize is still unclaimed this financial year.
    """
    if not permissions.is_admin_or_manager(request.user):
        messages.error(request, "You do not have permission to view incentive payouts.")
        return redirect("clients:admin_dashboard")

    today = timezone.localdate()
    try:
        month = int(request.GET.get("month", today.month))
        year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    if not 1 <= month <= 12:
        month = today.month
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])

    approved = Sale.objects.filter(status=Sale.STATUS_APPROVED, date__gte=start, date__lte=end)
    by_emp = {
        r["employee_id"]: r
        for r in approved.values("employee_id").annotate(
            pts=Sum("points"), bonus=Sum("bonus_points"))
    }

    life_rule = (IncentiveRule.objects.filter(
        active=True, slab_mode=IncentiveRule.MODE_BONUS, slabs__isnull=False)
        .prefetch_related("slabs").distinct().first())

    rows = []
    totals = {"base": Decimal("0"), "bonus": Decimal("0"),
              "accrued": Decimal("0"), "total": Decimal("0")}
    for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username"):
        agg = by_emp.get(e.id, {})
        pts = agg.get("pts") or Decimal("0")
        bonus = agg.get("bonus") or Decimal("0")
        accrued = incentives_service.accrued_points(e, start, end)
        base = pts - bonus
        total = pts + accrued

        ladder = None
        if life_rule is not None:
            vol, released, _p = incentives_service.period_totals(life_rule, e, end)
            nxt = incentives_service.next_rung(life_rule, vol)
            ladder = {"volume": vol, "released": released, "next": nxt,
                      "level": incentives_service.bonus_released_for(life_rule, vol)}

        if not (total or (ladder and ladder["volume"])):
            continue
        totals["base"] += base
        totals["bonus"] += bonus
        totals["accrued"] += accrued
        totals["total"] += total
        rows.append({"employee": e, "base": base, "bonus": bonus,
                     "accrued": accrued, "total": total, "ladder": ladder})

    # Every bonus release in the month, with the sale that triggered it.
    releases = (approved.filter(bonus_points__gt=0)
                .select_related("employee__user", "client")
                .order_by("date", "id"))

    return render(request, "incentives/payout.html", {
        "crumbs": [{"label": "Admin", "url": reverse("clients:admin_dashboard")},
                   {"label": "Incentive Payout"}],
        "kpis": [
            {"label": "Payable this month", "value": f"₹{inr(totals['total'])}", "color": "#4338CA"},
            {"label": "Of which ladder bonus", "value": f"₹{inr(totals['bonus'])}", "color": "#B45309"},
            {"label": "Multiyear credited", "value": f"₹{inr(totals['accrued'])}", "color": "#15803D"},
        ],
        "rows": rows, "totals": totals, "releases": releases,
        # Filtered here rather than with an {% if %} inside the loop: the
        # template could otherwise render a header over an empty body.
        "ladder_rows": [r for r in rows if r["ladder"] and r["ladder"]["volume"]],
        "life_rule": life_rule,
        "months": [(i, month_name[i]) for i in range(1, 13)],
        "years": list(range(today.year - 3, today.year + 1)),
        "sel_month": month, "sel_year": year, "month_label": month_name[month],
        "fy_label": f"{incentives_service.fy_start_year(end)}–{incentives_service.fy_start_year(end) + 1}",
    })


ASSUMPTION_ROWS = 6


@login_required
def incentive_calculator(request):
    """"What would I earn if…" — for every employee, not just admins.

    Starts from what the employee has actually banked in each rule's current
    period (so a ladder already half-climbed counts), then adds the typed
    assumptions on top. The maths is ``services.incentives``, the same module
    that pays a real sale, so this can't quote a rate the system won't honour.
    """
    viewer = getattr(request.user, "employee", None)
    can_pick = permissions.is_admin_or_manager(request.user)
    employees = (Employee.objects.select_related("user").filter(active=True).order_by("user__username")
                 if can_pick else [])

    target = viewer
    if can_pick and request.GET.get("employee"):
        target = Employee.objects.filter(pk=request.GET["employee"]).select_related("user").first() or viewer
    if target is None:
        messages.error(request, "Your account is not mapped to an employee, so there is nothing to calculate.")
        return redirect("clients:dashboard")

    today = timezone.localdate()
    rules = list(
        IncentiveRule.objects.filter(active=True)
        .select_related("product_ref").prefetch_related("slabs").order_by("product")
    )

    # Typed assumptions, grouped by rule: {rule_id: {"fresh": ₹, "port": ₹}}
    added = {}
    rows = []
    for i in range(ASSUMPTION_ROWS):
        rule_id = request.GET.get(f"rule_{i}") or ""
        amount = _dec_param(request.GET.get(f"amount_{i}"))
        policy_type = request.GET.get(f"ptype_{i}") or "fresh"
        years = max(1, int(request.GET.get(f"years_{i}") or 1))
        rows.append({"index": i, "rule_id": rule_id, "amount": amount or "",
                     "policy_type": policy_type, "policy_years": years})
        rule = next((r for r in rules if str(r.id) == rule_id), None)
        if rule is None or amount <= 0:
            continue
        is_health = bool(rule.product_ref and rule.product_ref.is_health)
        credit = (amount / years) if (is_health and years > 1) else amount
        bucket = added.setdefault(rule.id, {"fresh": Decimal("0"), "port": Decimal("0")})
        bucket["port" if (is_health and policy_type == "port") else "fresh"] += credit

    lines = []
    banked_total = Decimal("0")
    added_total = Decimal("0")
    for rule in rules:
        is_health = bool(rule.product_ref and rule.product_ref.is_health)
        volume, _bonus, booked = incentives_service.period_totals(
            rule, target, today, is_health=is_health)
        extra = added.get(rule.id)
        proj = incentives_service.project(
            rule, volume,
            extra["fresh"] if extra else Decimal("0"),
            added_port=extra["port"] if extra else Decimal("0"),
        )
        banked_total += booked
        added_total += proj["added"]
        if not (booked or volume or extra):
            continue  # nothing banked, nothing assumed — don't pad the table
        lines.append({
            "rule": rule,
            "is_health": is_health,
            "period_label": ("this month" if rule.slab_period == IncentiveRule.PERIOD_MONTH
                             else "this financial year"),
            "volume": volume,
            "booked": booked,
            "assumed": (proj["final_volume"] - volume) + (extra["port"] if extra else Decimal("0")),
            "port_assumed": extra["port"] if extra else Decimal("0"),
            "added": proj["added"],
            "final_volume": proj["final_volume"],
            "rate": proj["rate"],
            # Employees read points, not rates — this is the same step said plainly.
            "per_lakh": proj["rate"] * Decimal("1000"),
            "bonus": proj["bonus"],
            "next": incentives_service.next_rung(rule, proj["final_volume"]),
        })

    # Where this person stands on every yearly ladder — the thing the results
    # table only hints at, and the reason someone opens this page in a payout week.
    ladder_status = []
    for rule in rules:
        if rule.slab_mode != IncentiveRule.MODE_BONUS or not rule.slabs.exists():
            continue
        st = incentives_service.life_bonus_status(
            rule, target, incentives_service.fy_start_year(today))
        st["rungs"] = incentives_service.ladder(rule)
        # Whoever can already see other people's figures gets the whole roster
        # here, so the year reads at a glance instead of one employee at a time.
        # period_totals rather than life_bonus_status: this needs four numbers,
        # not a twelve-month strip per person.
        st["roster"] = []
        if can_pick:
            for e in employees:
                vol, released, _pts = incentives_service.period_totals(rule, e, today)
                if not vol:
                    continue
                level = incentives_service.bonus_released_for(rule, vol)
                st["roster"].append({
                    "employee": e, "volume": vol, "released": released,
                    "level": level, "pending": max(level - released, Decimal("0")),
                })
            st["roster"].sort(key=lambda r: r["volume"], reverse=True)
            st["roster_totals"] = {
                k: sum((r[k] for r in st["roster"]), Decimal("0"))
                for k in ("volume", "released", "pending")
            }
        ladder_status.append(st)

    explainers = [e for e in (
        incentives_service.explain(r, is_health=bool(r.product_ref and r.product_ref.is_health))
        for r in rules) if e]

    return render(request, "incentives/calculator.html", {
        "explainers": explainers,
        "ladder_status": ladder_status,
        "fy_label": f"{incentives_service.fy_start_year(today)}–{incentives_service.fy_start_year(today) + 1}",
        "pending_accruals": incentives_service.pending_accruals(target),
        "crumbs": [{"label": "Sales", "url": reverse("clients:all_sales")},
                   {"label": "Incentive Calculator"}],
        "kpis": [
            {"label": "Earned so far", "value": f"₹{inr(banked_total)}", "color": "#15803D"},
            {"label": "Assumptions add", "value": f"₹{inr(added_total)}", "color": "#B45309"},
            {"label": "Would total", "value": f"₹{inr(banked_total + added_total)}", "color": "#4338CA"},
        ],
        "target": target, "employees": employees, "can_pick": can_pick,
        "rules": rules, "rows": rows, "lines": lines,
        "banked_total": banked_total, "added_total": added_total,
        "grand_total": banked_total + added_total,
        "has_assumptions": bool(added),
        "today": today,
    })


@login_required
def incentive_structure(request):
    """The incentive structure, explained — plus a what-if calculator.

    The calculator runs the same ``services.incentives.quote()`` that pays a
    real sale, so what this page prints is what the sale would actually earn.
    """
    if not permissions.can(request.user, "manage_incentives"):
        messages.error(request, "You do not have permission to view the incentive structure.")
        return redirect("clients:admin_dashboard")

    rules = list(
        IncentiveRule.objects.filter(active=True)
        .select_related("product_ref").prefetch_related("slabs")
        .order_by("product")
    )
    cards = [{
        "rule": r,
        "base_percent": incentives_service.unit_rate_percent(r),
        "ladder": incentives_service.ladder(r),
        "is_health": bool(r.product_ref and r.product_ref.is_health),
    } for r in rules]

    trial = None
    picked = request.GET.get("rule") or ""
    if picked:
        rule = next((r for r in rules if str(r.id) == picked), None)
        amount = _dec_param(request.GET.get("amount"))
        prior = _dec_param(request.GET.get("prior_volume"))
        years = max(1, int(request.GET.get("policy_years") or 1))
        policy_type = request.GET.get("policy_type") or ""
        is_health = bool(rule and rule.product_ref and rule.product_ref.is_health)
        credit = (amount / years) if (is_health and years > 1) else amount
        result = incentives_service.quote(
            rule, credit,
            prior_volume=prior,
            prior_bonus=incentives_service.bonus_released_for(rule, prior),
            policy_type=policy_type if is_health else "",
            is_health=is_health,
        )
        trial = {
            "rule": rule, "amount": amount, "credit": credit, "prior_volume": prior,
            "policy_years": years, "policy_type": policy_type, "is_health": is_health,
            "result": result,
            "effective": (result["total"] / amount * Decimal("100")) if amount else Decimal("0"),
        }

    return render(request, "incentives/structure.html", {
        "crumbs": [
            {"label": "Admin", "url": reverse("clients:admin_dashboard")},
            {"label": "Incentive Structure"},
        ],
        "kpis": [
            {"label": "Active Rules", "value": len(rules), "color": "#4338CA"},
            {"label": "Products with Ladders",
             "value": sum(1 for c in cards if c["ladder"]), "color": "#B45309"},
            {"label": "Edit Rules", "value": "Open", "color": "#15803D",
             "url": reverse("clients:manage_incentive_rules")},
        ],
        "cards": cards,
        "rules": rules,
        "trial": trial,
    })


def _dec_param(raw):
    """A rupee figure typed into the calculator, or 0 for anything unusable."""
    try:
        return Decimal(str(raw or "0").replace(",", "").strip() or "0")
    except (ArithmeticError, ValueError):
        return Decimal("0")


@login_required
@require_POST
def update_incentive_rule(request, rule_id):
    """AJAX: Update unit_amount, points_per_unit, active for a rule."""
    if not permissions.can(request.user, "manage_incentives"):
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
    except Exception:
        logger.exception("Incentive rule/slab update failed")
        return JsonResponse({"error": "Could not save — check the values and try again."}, status=400)


@login_required
@require_POST
def add_incentive_rule(request):
    """AJAX: Add a new incentive rule."""
    if not permissions.can(request.user, "manage_incentives"):
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
    except Exception:
        logger.exception("Incentive rule/slab update failed")
        return JsonResponse({"error": "Could not save — check the values and try again."}, status=400)


@login_required
@require_POST
def delete_incentive_rule(request, rule_id):
    """AJAX: Delete an incentive rule and all its slabs."""
    if not permissions.can(request.user, "manage_incentives"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    rule = get_object_or_404(IncentiveRule, id=rule_id)
    product_name = rule.product
    rule.delete()
    return JsonResponse({"success": True, "message": f"Rule for '{product_name}' deleted."})


def _reject_bad_slab_payout(rule, payout):
    """A rate-mode slab payout is a percent, not rupees.

    The mobile screen used to label every payout "pts", so an admin editing a
    health band from a phone could type 500 meaning rupees and write a 500%
    rate straight into payroll. Guarded here rather than in each client, so the
    web page, the app and any old build still in the field all hit it.
    """
    from ..models import IncentiveRule

    if rule.slab_mode == IncentiveRule.MODE_RATE and payout > Decimal("100"):
        return JsonResponse(
            {"error": f"{rule.product} pays a percentage per band, so the value must "
                      f"be 100 or less — {payout} looks like a rupee amount."},
            status=400,
        )
    return None


@login_required
@require_POST
def add_incentive_slab(request, rule_id):
    """AJAX: Add a slab to a rule."""
    if not permissions.can(request.user, "manage_incentives"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    rule = get_object_or_404(IncentiveRule, id=rule_id)
    try:
        data = json.loads(request.body)
        threshold = Decimal(str(data.get("threshold", 0)))
        payout = Decimal(str(data.get("payout", 0)))
        label = data.get("label", "").strip()

        if threshold < 0 or payout <= 0:
            return JsonResponse({"error": "Threshold and payout must be positive."}, status=400)
        bad = _reject_bad_slab_payout(rule, payout)
        if bad:
            return bad

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
    except Exception:
        logger.exception("Incentive rule/slab update failed")
        return JsonResponse({"error": "Could not save — check the values and try again."}, status=400)


@login_required
@require_POST
def update_incentive_slab(request, slab_id):
    """AJAX: Update an existing slab."""
    if not permissions.can(request.user, "manage_incentives"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    slab = get_object_or_404(IncentiveSlab, id=slab_id)
    try:
        data = json.loads(request.body)
        if "threshold" in data:
            slab.threshold = Decimal(str(data["threshold"]))
        if "payout" in data:
            slab.payout = Decimal(str(data["payout"]))
            bad = _reject_bad_slab_payout(slab.rule, slab.payout)
            if bad:
                return bad
        if "label" in data:
            slab.label = data["label"].strip()
        slab.save()
        return JsonResponse({"success": True, "message": "Slab updated."})
    except Exception:
        logger.exception("Incentive rule/slab update failed")
        return JsonResponse({"error": "Could not save — check the values and try again."}, status=400)


@login_required
@require_POST
def delete_incentive_slab(request, slab_id):
    """AJAX: Delete a slab."""
    if not permissions.can(request.user, "manage_incentives"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    slab = get_object_or_404(IncentiveSlab, id=slab_id)
    slab.delete()
    return JsonResponse({"success": True, "message": "Slab deleted."})


@login_required
@require_POST
def recalc_points(request):
    """Rebuild points on every sale using the current rules. Admin-level action
    (retroactively changes payouts), so it is POST-only and never open to
    plain employees."""
    user_emp = getattr(request.user, "employee", None)
    is_admin_user = permissions.is_admin(request.user)
    is_manager = bool(user_emp and user_emp.role == "manager")
    manager_access = get_manager_access() if is_manager else None

    if not permissions.can(request.user, "recalc_points"):
        return HttpResponseForbidden("You do not have permission to recalculate points.")

    count = 0
    for s in Sale.objects.all().iterator():
        s.save()  # save() recomputes points
        count += 1

    messages.success(request, f"Recalculated points for {count} sales.")
    return redirect("clients:all_sales")


@login_required
def edit_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    user_emp = getattr(request.user, "employee", None)
    is_admin_user = permissions.is_admin(request.user)
    is_manager = bool(user_emp and user_emp.role == "manager")
    mgr_access = get_manager_access() if is_manager else None
    if (
        not is_admin_user
        and not permissions.can(request.user, "edit_sales")
        and (not user_emp or sale.employee != user_emp)
    ):
        return HttpResponseForbidden("You do not have permission to edit this sale.")

    if request.method == "POST":
        form = EditSaleForm(request.POST, instance=sale)
        if form.is_valid():
            updated = form.save(commit=False)
            if updated.product:
                updated.product_ref = Product.objects.filter(name=updated.product).first()
            sales_service.snapshot_ppt_margin(updated)
            # Edits by anyone other than an admin invalidate a prior approval:
            # the sale goes back to pending so an admin re-reviews the new numbers.
            needs_reapproval = not is_admin_user and updated.status != Sale.STATUS_PENDING
            if needs_reapproval:
                updated.status = Sale.STATUS_PENDING
                updated.approved_by = None
                updated.approved_at = None
                updated.rejection_reason = ""
            updated._audit_actor = request.user
            updated.save()
            sales_service.recompute_sibling_sales(updated)
            if needs_reapproval:
                messages.success(request, "Sale updated — it is pending approval again.")
            else:
                messages.success(request, "Sale updated successfully!")
            return redirect("clients:all_sales")
    else:
        form = EditSaleForm(instance=sale)

    return render(request, "sales/edit_sale.html", {"form": form, "sale": sale, **_sale_product_meta(show_margin=is_admin_user)})


@login_required
def delete_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    user_emp = getattr(request.user, "employee", None)
    is_admin_user = permissions.is_admin(request.user)
    if not is_admin_user and (not user_emp or sale.employee != user_emp):
        return HttpResponseForbidden("You do not have permission to delete this sale.")
    # Non-admins may only withdraw their own sales while still pending;
    # once approved/rejected the record is part of the reviewed books.
    if not is_admin_user and sale.status != Sale.STATUS_PENDING:
        return HttpResponseForbidden("Only an admin can delete a sale that has already been reviewed.")
    if request.method == "POST":
        sales_service.delete_sale(sale, request.user)
        messages.success(request, "Sale deleted successfully!")
        return redirect("clients:admin_dashboard")
    return render(request, "sales/delete_sale.html", {"sale": sale})



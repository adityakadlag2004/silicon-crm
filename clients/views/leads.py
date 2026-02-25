"""Lead management views."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.db import transaction
from django.db.models import Sum, Q, Count
from django.core.paginator import Paginator

from ..models import (
    Client,
    Employee,
    Lead,
    LeadProductProgress,
    LeadFollowUp,
    LeadRemark,
    Sale,
)
from ..forms import (
    LeadForm,
    LeadFamilyMemberFormSet,
    LeadProductProgressFormSet,
)
from .helpers import _lead_queryset_for_request, _parse_decimal


@login_required
def lead_list_by_stage(request, stage):
    stage_labels = {
        Lead.STAGE_PENDING: "Pending Leads",
        Lead.STAGE_HALF: "Half Sold Leads",
        Lead.STAGE_PROCESSED: "Processed Leads",
    }

    if stage not in stage_labels:
        return redirect("clients:lead_stage_list", stage=Lead.STAGE_PENDING)

    base_qs = _lead_queryset_for_request(request).filter(is_discarded=False)
    search_term = request.GET.get("q", "").strip()
    if search_term:
        base_qs = base_qs.filter(customer_name__icontains=search_term)

    leads = base_qs.filter(stage=stage).order_by("-updated_at")
    for lead in leads:
        lead.progress_map = {p.product: p for p in lead.progress_entries.all()}
    counts = {
        Lead.STAGE_PENDING: base_qs.filter(stage=Lead.STAGE_PENDING).count(),
        Lead.STAGE_HALF: base_qs.filter(stage=Lead.STAGE_HALF).count(),
        Lead.STAGE_PROCESSED: base_qs.filter(stage=Lead.STAGE_PROCESSED).count(),
    }

    context = {
        "leads": leads,
        "stage": stage,
        "stage_label": stage_labels.get(stage, "Leads"),
        "counts": counts,
        "search_term": search_term,
    }
    return render(request, "clients/leads/lead_list.html", context)


@login_required
def lead_detail(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    lead.progress_map = {p.product: p for p in lead.progress_entries.all()}
    followups = lead.followups.select_related("assigned_to__user").order_by("-scheduled_time")
    remarks = lead.remarks.select_related("created_by").order_by("-created_at")
    now_ts = timezone.now()
    for f in followups:
        f.is_overdue = f.status == "pending" and f.scheduled_time < now_ts
    return render(request, "clients/leads/lead_detail.html", {
        "lead": lead,
        "followups": followups,
        "remarks": remarks,
    })


@login_required
@require_POST
def lead_add_followup(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    when_raw = request.POST.get("scheduled_time")
    note = (request.POST.get("note") or "").strip()
    if not when_raw:
        messages.error(request, "Please choose a follow-up date/time.")
        return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))

    try:
        when_dt = parse_datetime(when_raw)
        if when_dt and timezone.is_naive(when_dt):
            when_dt = timezone.make_aware(when_dt)
    except Exception:
        when_dt = None

    if not when_dt:
        messages.error(request, "Invalid date/time format.")
        return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))

    LeadFollowUp.objects.create(
        lead=lead,
        assigned_to=lead.assigned_to,
        scheduled_time=when_dt,
        note=note,
        created_by=request.user,
    )
    messages.success(request, "Follow-up added.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_add_remark(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    text = (request.POST.get("text") or "").strip()
    if not text:
        messages.error(request, "Remark cannot be empty.")
        return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))
    LeadRemark.objects.create(lead=lead, text=text, created_by=request.user)
    messages.success(request, "Remark added.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
def lead_bulk_import(request):
    emp = getattr(request.user, "employee", None)
    if not (request.user.is_superuser or emp):
        return HttpResponseForbidden()

    role = getattr(emp, "role", "") if emp else ""
    is_admin_or_manager = request.user.is_superuser or role in ("admin", "manager")
    active_employees = list(
        Employee.objects.filter(active=True)
        .select_related("user")
        .order_by("user__username")
    )

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            rows = body.get("rows", [])
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"created": 0, "errors": ["Invalid request body."]}, status=400)

        if not rows:
            return JsonResponse({"created": 0, "errors": ["No rows submitted."]})

        employee_map = {e.id: e for e in active_employees}
        valid_stages = {c[0] for c in Lead.STAGE_CHOICES}
        created = 0
        errors = []

        for idx, row in enumerate(rows, start=1):
            try:
                customer_name = (row.get("customer_name") or "").strip()
                if not customer_name:
                    raise ValueError("Customer name is required")

                if is_admin_or_manager:
                    assigned_id = row.get("assigned_to")
                    if assigned_id:
                        try:
                            assigned_id = int(assigned_id)
                        except (ValueError, TypeError):
                            raise ValueError("Invalid Assign To value")
                        emp_obj = employee_map.get(assigned_id)
                        if not emp_obj:
                            raise ValueError(f"Employee id {assigned_id} not found")
                    else:
                        emp_obj = emp
                else:
                    emp_obj = emp

                if not emp_obj:
                    raise ValueError("Cannot determine assigned employee")

                phone = (row.get("phone") or "").strip() or ""
                email = (row.get("email") or "").strip() or ""
                notes = (row.get("notes") or "").strip()

                stage_val = (row.get("stage") or Lead.STAGE_PENDING).strip()
                if stage_val not in valid_stages:
                    stage_val = Lead.STAGE_PENDING

                data_received = bool(row.get("data_received"))

                with transaction.atomic():
                    lead = Lead(
                        customer_name=customer_name,
                        phone=phone,
                        email=email,
                        data_received=data_received,
                        notes=notes,
                        assigned_to=emp_obj,
                        created_by=request.user,
                        stage=stage_val,
                    )
                    lead.save()

                    for product in ("health", "life", "wealth"):
                        target_raw = row.get(f"{product}_target")
                        achieved_raw = row.get(f"{product}_achieved")
                        target = _parse_decimal(target_raw)
                        achieved = _parse_decimal(achieved_raw)

                        if target is not None or achieved is not None:
                            t = target or Decimal("0")
                            a = achieved or Decimal("0")
                            if t > 0 and a >= t:
                                status = LeadProductProgress.STATUS_PROCESSED
                            elif a > 0:
                                status = LeadProductProgress.STATUS_HALF
                            else:
                                status = LeadProductProgress.STATUS_PENDING

                            LeadProductProgress.objects.create(
                                lead=lead,
                                product=product,
                                target_amount=target,
                                achieved_amount=achieved,
                                status=status,
                            )

                    lead.recompute_stage()

                created += 1
            except Exception as exc:
                errors.append(f"Row {idx}: {exc}")

        return JsonResponse({"created": created, "errors": errors})

    ctx = {
        "is_admin_or_manager": is_admin_or_manager,
        "employees": active_employees,
    }
    return render(request, "clients/leads/lead_bulk_import.html", ctx)


@login_required
def lead_management(request):
    base_qs = _lead_queryset_for_request(request)
    emp = getattr(request.user, "employee", None)
    role = getattr(emp, "role", "")
    show_my_tab = role in ["admin", "manager"]
    can_see_stats = role in ["admin", "manager"] or request.user.is_superuser

    view_mode = request.GET.get("view", "current")
    if view_mode == "my" and show_my_tab:
        base_qs = base_qs.filter(assigned_to=emp, is_discarded=False).exclude(stage=Lead.STAGE_PROCESSED)
    elif view_mode == "completed":
        base_qs = base_qs.filter(is_discarded=False, stage=Lead.STAGE_PROCESSED)
    elif view_mode == "discarded":
        base_qs = base_qs.filter(is_discarded=True)
    else:
        view_mode = "current"
        base_qs = base_qs.filter(is_discarded=False).exclude(stage=Lead.STAGE_PROCESSED)

    search_term = request.GET.get("q", "").strip()
    if search_term:
        base_qs = base_qs.filter(customer_name__icontains=search_term)

    assigned_to_filter = request.GET.get("assigned_to", "")
    if assigned_to_filter and can_see_stats:
        base_qs = base_qs.filter(assigned_to_id=assigned_to_filter)

    stage_filter = request.GET.get("stage", "")
    if stage_filter and stage_filter in dict(Lead.STAGE_CHOICES):
        base_qs = base_qs.filter(stage=stage_filter)

    data_received_filter = request.GET.get("data_received", "")
    if data_received_filter == "yes":
        base_qs = base_qs.filter(data_received=True)
    elif data_received_filter == "no":
        base_qs = base_qs.filter(data_received=False)

    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    if date_from:
        try:
            base_qs = base_qs.filter(created_at__date__gte=datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError:
            pass
    if date_to:
        try:
            base_qs = base_qs.filter(created_at__date__lte=datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError:
            pass

    leads_qs = base_qs.order_by("-updated_at")

    PER_PAGE = 25
    paginator = Paginator(leads_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

    current_page = page_obj.number
    total_pages = paginator.num_pages
    start_page = max(current_page - 3, 1)
    end_page = min(current_page + 3, total_pages)
    page_range = range(start_page, end_page + 1)

    get_params = request.GET.copy()
    if "page" in get_params:
        del get_params["page"]
    base_qs_params = get_params.urlencode()

    for lead in page_obj:
        lead.progress_map = {p.product: p for p in lead.progress_entries.all()}

    stage_counts = {
        Lead.STAGE_PENDING: base_qs.filter(stage=Lead.STAGE_PENDING).count(),
        Lead.STAGE_HALF: base_qs.filter(stage=Lead.STAGE_HALF).count(),
        Lead.STAGE_PROCESSED: base_qs.filter(stage=Lead.STAGE_PROCESSED).count(),
    }

    tab_base = _lead_queryset_for_request(request)
    tab_counts = {
        "current": tab_base.filter(is_discarded=False).exclude(stage=Lead.STAGE_PROCESSED).count(),
        "completed": tab_base.filter(is_discarded=False, stage=Lead.STAGE_PROCESSED).count(),
        "discarded": tab_base.filter(is_discarded=True).count(),
    }
    if show_my_tab:
        tab_counts["my"] = tab_base.filter(assigned_to=emp, is_discarded=False).exclude(stage=Lead.STAGE_PROCESSED).count()

    progress_counts = {}
    if can_see_stats:
        agg = (
            LeadProductProgress.objects.filter(lead__in=base_qs.values_list("id", flat=True))
            .values("product", "status")
            .order_by()
            .annotate(total=Count("id"))
        )
        for row in agg:
            product = row["product"]
            status = row["status"]
            progress_counts.setdefault(product, {})[status] = row["total"]

    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username") if can_see_stats else []

    context = {
        "leads": page_obj,
        "page_obj": page_obj,
        "page_range": page_range,
        "base_qs_params": base_qs_params,
        "search_term": search_term,
        "stage_counts": stage_counts,
        "progress_counts": progress_counts if can_see_stats else {},
        "can_see_stats": can_see_stats,
        "can_bulk_import": True,
        "view_mode": view_mode,
        "tab_counts": tab_counts,
        "show_my_tab": show_my_tab,
        "show_my_progress": bool(emp) and role in ["employee", "manager"],
        "employees": employees,
        "assigned_to_filter": assigned_to_filter,
        "stage_filter": stage_filter,
        "data_received_filter": data_received_filter,
        "date_from": date_from,
        "date_to": date_to,
    }
    return render(request, "clients/leads/lead_management.html", context)


@login_required
@require_POST
def lead_mark_complete(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    lead.is_discarded = False
    lead.stage = Lead.STAGE_PROCESSED
    lead.save(update_fields=["is_discarded", "stage", "updated_at"])
    messages.success(request, "Lead marked as completed.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_discard(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    lead.is_discarded = True
    lead.save(update_fields=["is_discarded", "updated_at"])
    messages.info(request, "Lead discarded.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_undiscard(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    lead.is_discarded = False
    lead.save(update_fields=["is_discarded", "updated_at"])
    messages.success(request, "Lead reopened.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_followup_done(request, followup_id):
    emp = getattr(request.user, "employee", None)
    qs = LeadFollowUp.objects.select_related("lead")
    if emp and getattr(emp, "role", "") == "employee":
        qs = qs.filter(assigned_to=emp)
    followup = get_object_or_404(qs, pk=followup_id)
    followup.status = "done"
    followup.save(update_fields=["status"])
    if request.headers.get("HX-Request"):
        return HttpResponse('<div class="text-success small fw-bold">Done &#10003;</div>')
    messages.success(request, "Follow-up marked as done.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_convert_to_client(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    if lead.converted_client:
        messages.info(request, "This lead has already been converted.")
        return redirect("clients:lead_detail", lead_id=lead.id)
    if lead.stage != Lead.STAGE_PROCESSED:
        messages.error(request, "Only processed leads can be converted to clients.")
        return redirect("clients:lead_detail", lead_id=lead.id)

    progress_map = {p.product: p for p in lead.progress_entries.all()}
    health_p = progress_map.get("health")
    life_p = progress_map.get("life")
    wealth_p = progress_map.get("wealth")

    with transaction.atomic():
        client = Client(
            name=lead.customer_name,
            phone=lead.phone or None,
            email=lead.email or None,
            mapped_to=lead.assigned_to,
            status="Mapped" if lead.assigned_to else "Unmapped",
        )
        if health_p and health_p.status == LeadProductProgress.STATUS_PROCESSED:
            client.health_status = True
            client.health_cover = health_p.achieved_amount
        if life_p and life_p.status == LeadProductProgress.STATUS_PROCESSED:
            client.life_status = True
            client.life_cover = life_p.achieved_amount
        if wealth_p and wealth_p.status == LeadProductProgress.STATUS_PROCESSED:
            client.sip_status = True
            client.sip_amount = wealth_p.achieved_amount
        client.save()
        lead.converted_client = client
        lead.save(update_fields=["converted_client", "updated_at"])

    messages.success(request, f"Lead converted to Client #{client.id} ({client.name}).")
    return redirect("clients:lead_detail", lead_id=lead.id)


@login_required
def lead_followups_api(request):
    """JSON endpoint for dashboard calendar."""
    emp = getattr(request.user, "employee", None)
    role = getattr(emp, "role", "") if emp else ""
    is_admin = request.user.is_superuser or role in ["admin", "manager"]

    now_ts = timezone.now()
    qs = LeadFollowUp.objects.filter(status="pending").select_related("lead", "assigned_to__user")

    filter_mode = request.GET.get("filter", "this_week")
    employee_id = request.GET.get("employee_id")

    if not is_admin and emp:
        qs = qs.filter(assigned_to=emp)
    elif is_admin and employee_id:
        qs = qs.filter(assigned_to_id=employee_id)

    if filter_mode == "today":
        day_start = now_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        qs = qs.filter(scheduled_time__gte=day_start, scheduled_time__lt=day_end)
    elif filter_mode == "tomorrow":
        day_start = (now_ts + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        qs = qs.filter(scheduled_time__gte=day_start, scheduled_time__lt=day_end)
    elif filter_mode == "overdue":
        qs = qs.filter(scheduled_time__lt=now_ts)
    elif filter_mode == "all":
        pass
    else:
        today = now_ts.date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=7)
        qs = qs.filter(
            scheduled_time__gte=timezone.make_aware(datetime.combine(week_start, datetime.min.time())),
            scheduled_time__lt=timezone.make_aware(datetime.combine(week_end, datetime.min.time())),
        )
        overdue = LeadFollowUp.objects.filter(
            status="pending",
            scheduled_time__lt=timezone.make_aware(datetime.combine(week_start, datetime.min.time())),
        ).select_related("lead", "assigned_to__user")
        if not is_admin and emp:
            overdue = overdue.filter(assigned_to=emp)
        elif is_admin and employee_id:
            overdue = overdue.filter(assigned_to_id=employee_id)
        qs = qs | overdue

    qs = qs.order_by("scheduled_time")
    followups = []
    for f in qs:
        followups.append({
            "id": f.id,
            "lead_id": f.lead_id,
            "lead_name": f.lead.customer_name,
            "scheduled_time": f.scheduled_time.isoformat(),
            "date": f.scheduled_time.strftime("%Y-%m-%d"),
            "time": f.scheduled_time.strftime("%H:%M"),
            "note": f.note,
            "assigned_to": f.assigned_to.user.username if f.assigned_to else "",
            "is_overdue": f.scheduled_time < now_ts,
            "lead_url": reverse("clients:lead_detail", args=[f.lead_id]),
        })
    today = now_ts.date()
    week_start = today - timedelta(days=today.weekday())
    week_dates_list = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]
    return JsonResponse({"followups": followups, "week_dates": week_dates_list, "today": today.isoformat()})


@login_required
@require_POST
def lead_followup_reschedule(request, followup_id):
    """Move a follow-up to a different date (drag-and-drop)."""
    emp = getattr(request.user, "employee", None)
    qs = LeadFollowUp.objects.select_related("lead")
    if emp and getattr(emp, "role", "") == "employee":
        qs = qs.filter(assigned_to=emp)
    followup = get_object_or_404(qs, pk=followup_id)

    try:
        data = json.loads(request.body)
        new_date_str = data.get("date")
        new_date = datetime.strptime(new_date_str, "%Y-%m-%d").date()
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"error": "Invalid date."}, status=400)

    old_time = followup.scheduled_time.time()
    new_dt = timezone.make_aware(datetime.combine(new_date, old_time))
    followup.scheduled_time = new_dt
    followup.save(update_fields=["scheduled_time"])
    return JsonResponse({"ok": True, "new_date": new_date.isoformat(), "new_time": old_time.strftime("%H:%M")})


@login_required
def lead_progress_overview_admin(request):
    emp = getattr(request.user, "employee", None)
    if not (request.user.is_superuser or (emp and getattr(emp, "role", "") in ["admin", "manager"])):
        return HttpResponseForbidden()

    base_qs = Lead.objects.filter(is_discarded=False).select_related("assigned_to__user")
    stage_counts = {
        Lead.STAGE_PENDING: base_qs.filter(stage=Lead.STAGE_PENDING).count(),
        Lead.STAGE_HALF: base_qs.filter(stage=Lead.STAGE_HALF).count(),
        Lead.STAGE_PROCESSED: base_qs.filter(stage=Lead.STAGE_PROCESSED).count(),
    }
    total = sum(stage_counts.values()) or 0
    stage_pct = {k: (v / total * 100) if total else 0 for k, v in stage_counts.items()}

    per_employee = list(
        base_qs.values("assigned_to__user__username")
        .annotate(
            pending=Count("id", filter=Q(stage=Lead.STAGE_PENDING)),
            half=Count("id", filter=Q(stage=Lead.STAGE_HALF)),
            processed=Count("id", filter=Q(stage=Lead.STAGE_PROCESSED)),
        )
        .order_by("assigned_to__user__username")
    )

    sales_map = {
        row["employee__user__username"]: row["total_sales"] or 0
        for row in Sale.objects.filter(status=Sale.STATUS_APPROVED)
        .values("employee__user__username")
        .annotate(total_sales=Sum("amount"))
    }
    for row in per_employee:
        row["total_sales"] = sales_map.get(row["assigned_to__user__username"], 0)

    progress_counts = (
        LeadProductProgress.objects.filter(lead__is_discarded=False)
        .values("product", "status")
        .order_by()
        .annotate(total=Count("id"))
    )
    progress_map = {}
    for row in progress_counts:
        product = row["product"]
        status = row["status"]
        progress_map.setdefault(product, {})[status] = row["total"]

    personal_stage = None
    personal_stage_pct = None
    if emp:
        mine = base_qs.filter(assigned_to=emp)
        personal_stage = {
            Lead.STAGE_PENDING: mine.filter(stage=Lead.STAGE_PENDING).count(),
            Lead.STAGE_HALF: mine.filter(stage=Lead.STAGE_HALF).count(),
            Lead.STAGE_PROCESSED: mine.filter(stage=Lead.STAGE_PROCESSED).count(),
        }
        personal_total = sum(personal_stage.values()) or 0
        personal_stage_pct = {k: (v / personal_total * 100) if personal_total else 0 for k, v in personal_stage.items()}

    context = {
        "scope_label": "Team Leads",
        "stage_counts": stage_counts,
        "stage_pct": stage_pct,
        "per_employee": per_employee,
        "progress_map": progress_map,
        "personal_stage": personal_stage,
        "personal_stage_pct": personal_stage_pct,
    }
    return render(request, "clients/leads/lead_progress_overview.html", context)


@login_required
def lead_progress_overview_employee(request):
    emp = getattr(request.user, "employee", None)
    if not emp:
        return HttpResponseForbidden()

    base_qs = Lead.objects.filter(assigned_to=emp, is_discarded=False)
    stage_counts = {
        Lead.STAGE_PENDING: base_qs.filter(stage=Lead.STAGE_PENDING).count(),
        Lead.STAGE_HALF: base_qs.filter(stage=Lead.STAGE_HALF).count(),
        Lead.STAGE_PROCESSED: base_qs.filter(stage=Lead.STAGE_PROCESSED).count(),
    }
    total = sum(stage_counts.values()) or 0
    stage_pct = {k: (v / total * 100) if total else 0 for k, v in stage_counts.items()}

    progress_counts = (
        LeadProductProgress.objects.filter(lead__assigned_to=emp, lead__is_discarded=False)
        .values("product", "status")
        .order_by()
        .annotate(total=Count("id"))
    )
    progress_map = {}
    for row in progress_counts:
        product = row["product"]
        status = row["status"]
        progress_map.setdefault(product, {})[status] = row["total"]

    context = {
        "scope_label": "My Leads",
        "stage_counts": stage_counts,
        "stage_pct": stage_pct,
        "per_employee": None,
        "progress_map": progress_map,
        "personal_stage": stage_counts,
        "personal_stage_pct": stage_pct,
    }
    return render(request, "clients/leads/lead_progress_overview.html", context)


@login_required
def lead_create(request):
    initial_lead = Lead()
    if hasattr(request.user, "employee") and getattr(request.user.employee, "role", "") == "employee":
        initial_lead.assigned_to = request.user.employee

    default_products = [
        {"product": LeadProductProgress.PRODUCT_HEALTH},
        {"product": LeadProductProgress.PRODUCT_LIFE},
        {"product": LeadProductProgress.PRODUCT_WEALTH},
    ]

    if request.method == "POST":
        form = LeadForm(request.POST, instance=initial_lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(request.POST, instance=initial_lead, prefix="family")
        product_formset = LeadProductProgressFormSet(request.POST, instance=initial_lead, prefix="product")

        if form.is_valid() and family_formset.is_valid() and product_formset.is_valid():
            lead = form.save(commit=False)
            if hasattr(request.user, "employee") and getattr(request.user.employee, "role", "") == "employee":
                lead.assigned_to = request.user.employee
            lead.created_by = request.user
            lead.save()

            family_formset.instance = lead
            product_formset.instance = lead
            family_formset.save()
            product_formset.save()
            lead.recompute_stage(save=True)

            messages.success(request, "Lead created successfully.")
            return redirect("clients:lead_stage_list", stage=lead.stage)
    else:
        form = LeadForm(instance=initial_lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(instance=initial_lead, prefix="family")
        product_formset = LeadProductProgressFormSet(instance=initial_lead, prefix="product", initial=default_products)

    return render(
        request,
        "clients/leads/lead_form.html",
        {
            "form": form,
            "family_formset": family_formset,
            "product_formset": product_formset,
            "mode": "create",
        },
    )


@login_required
def lead_update(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)

    if request.method == "POST":
        form = LeadForm(request.POST, instance=lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(request.POST, instance=lead, prefix="family")
        product_formset = LeadProductProgressFormSet(request.POST, instance=lead, prefix="product")

        if form.is_valid() and family_formset.is_valid() and product_formset.is_valid():
            lead = form.save(commit=False)
            if hasattr(request.user, "employee") and getattr(request.user.employee, "role", "") == "employee":
                lead.assigned_to = request.user.employee
            lead.save()

            family_formset.save()
            product_formset.save()
            lead.recompute_stage(save=True)
            messages.success(request, "Lead updated successfully.")
            return redirect("clients:lead_detail", lead_id=lead.id)
    else:
        form = LeadForm(instance=lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(instance=lead, prefix="family")
        product_initial = None
        if lead.progress_entries.count() == 0:
            product_initial = [
                {"product": LeadProductProgress.PRODUCT_HEALTH},
                {"product": LeadProductProgress.PRODUCT_LIFE},
                {"product": LeadProductProgress.PRODUCT_WEALTH},
            ]
        product_formset = LeadProductProgressFormSet(instance=lead, prefix="product", initial=product_initial)

    return render(
        request,
        "clients/leads/lead_form.html",
        {
            "form": form,
            "family_formset": family_formset,
            "product_formset": product_formset,
            "lead": lead,
            "mode": "update",
        },
    )

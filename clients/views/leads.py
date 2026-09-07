"""Lead pipeline views — SPANCO (Suspect → Prospect → Approach → Negotiation
→ Conclusion → Order).

Three screens: the working list (`lead_management`), the pipeline board
(`lead_board`) and the funnel report (`lead_pipeline_report`). Every stage
move goes through `services.leads`, never by assigning `lead.stage` here.
"""
import json
from datetime import datetime, timedelta

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

from .. import permissions
from ..models import (
    Employee,
    Lead,
    Product,
    Sale,
    Task,
)
from ..forms import (
    LeadForm,
    LeadFamilyMemberFormSet,
    LeadInterestFormSet,
)
from ..services import followups
from ..services import leads as lead_service
from .helpers import _lead_queryset_for_request, name_words_q, query_without

PER_PAGE = 25


def _stage_kpis(request, counts, active=""):
    """The SPANCO strip — six tiles, each one the filter for its stage.

    A tile switches the stage and NOTHING else: it carries the employee, the
    search and the dates already applied. Built from a bare
    `?stage=…` these tiles silently reset the picked employee back to the whole
    team every time somebody looked at another stage.
    """
    keep = query_without(request, "stage", "page")
    base = f"{reverse('clients:lead_management')}?{keep}&" if keep else (
        f"{reverse('clients:lead_management')}?")
    return [
        {
            "label": label,
            "value": counts.get(stage, 0),
            "color": Lead.STAGE_COLORS[stage],
            "sub": Lead.STAGE_HELP[stage],
            "url": f"{base}stage={stage}",
            "active": stage == active,
        }
        for stage, label in Lead.STAGE_CHOICES
    ]


def _apply_lead_filters(request, qs, can_see_all):
    """Search / assignee / stage / data-received / date filters, shared by the
    list and the board so the two never disagree about what's in the pipeline."""
    search_term = request.GET.get("q", "").strip()
    if search_term:
        qs = qs.filter(name_words_q("customer_name", search_term))

    assigned_to = request.GET.get("assigned_to", "")
    if assigned_to and can_see_all and assigned_to.isdigit():
        # "Show me this person's leads" means the ones they own AND the ones
        # they were brought onto — a shared lead is theirs to work either way.
        qs = qs.filter(Lead.team_q(int(assigned_to)))

    data_received = request.GET.get("data_received", "")
    if data_received == "yes":
        qs = qs.filter(data_received=True)
    elif data_received == "no":
        qs = qs.filter(data_received=False)

    for key, lookup in (("date_from", "gte"), ("date_to", "lte")):
        raw = request.GET.get(key, "")
        if raw:
            try:
                qs = qs.filter(**{f"created_at__date__{lookup}": datetime.strptime(raw, "%Y-%m-%d").date()})
            except ValueError:
                pass
    return qs


@login_required
def lead_management(request):
    """The working list: open / won / lost / mine, filtered and paginated."""
    emp = getattr(request.user, "employee", None)
    can_see_all = permissions.is_admin_or_manager(request.user)

    base_qs = _apply_lead_filters(request, _lead_queryset_for_request(request), can_see_all)

    view_mode = request.GET.get("view", "open")
    if view_mode == "mine" and emp:
        scoped = base_qs.filter(Lead.team_q(emp), is_discarded=False)
    elif view_mode == "won":
        scoped = base_qs.filter(is_discarded=False, stage=Lead.STAGE_ORDER)
    elif view_mode == "lost":
        scoped = base_qs.filter(is_discarded=True)
    else:
        view_mode = "open"
        scoped = base_qs.filter(is_discarded=False).exclude(stage=Lead.STAGE_ORDER)

    stage_filter = request.GET.get("stage", "")
    leads_qs = scoped.filter(stage=stage_filter) if stage_filter in dict(Lead.STAGE_CHOICES) else scoped

    paginator = Paginator(leads_qs.order_by("-stage_changed_at", "-updated_at"), PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    get_params = request.GET.copy()
    get_params.pop("page", None)

    tab_counts = {
        "open": base_qs.filter(is_discarded=False).exclude(stage=Lead.STAGE_ORDER).count(),
        "won": base_qs.filter(is_discarded=False, stage=Lead.STAGE_ORDER).count(),
        "lost": base_qs.filter(is_discarded=True).count(),
        "mine": base_qs.filter(Lead.team_q(emp), is_discarded=False).count() if emp else 0,
    }

    return render(request, "clients/leads/lead_management.html", {
        "kpis": _stage_kpis(request, lead_service.stage_counts(scoped), stage_filter),
        # The tabs switch the view and keep everything else, same as the tiles.
        "tab_qs": query_without(request, "view", "page"),
        "leads": page_obj,
        "page_obj": page_obj,
        "page_range": range(
            max(page_obj.number - 3, 1), min(page_obj.number + 3, paginator.num_pages) + 1,
        ),
        "base_qs_params": get_params.urlencode(),
        "search_term": request.GET.get("q", "").strip(),
        "stages": Lead.STAGE_CHOICES,
        "stage_filter": stage_filter,
        "view_mode": view_mode,
        "tab_counts": tab_counts,
        "show_mine_tab": bool(emp),
        "can_see_stats": can_see_all,
        "employees": (
            Employee.objects.filter(active=True).select_related("user").order_by("user__username")
            if can_see_all else []
        ),
        "assigned_to_filter": request.GET.get("assigned_to", ""),
        "data_received_filter": request.GET.get("data_received", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
    })


@login_required
def lead_board(request):
    """The pipeline as six SPANCO columns — where every live lead is standing."""
    can_see_all = permissions.is_admin_or_manager(request.user)
    qs = _apply_lead_filters(
        request, _lead_queryset_for_request(request).filter(is_discarded=False), can_see_all,
    )

    by_stage = {stage: [] for stage in Lead.STAGE_SEQUENCE}
    for lead in qs.order_by("stage_changed_at")[:400]:
        by_stage.get(lead.stage, by_stage[Lead.STAGE_SUSPECT]).append(lead)

    columns = [
        {
            "stage": stage,
            "label": label,
            "help": Lead.STAGE_HELP[stage],
            "color": Lead.STAGE_COLORS[stage],
            "leads": by_stage[stage],
            "count": len(by_stage[stage]),
        }
        for stage, label in Lead.STAGE_CHOICES
    ]

    return render(request, "clients/leads/lead_board.html", {
        "crumbs": [
            {"label": "Leads", "url": reverse("clients:lead_management")},
            {"label": "Pipeline Board"},
        ],
        "kpis": _stage_kpis(request, {c["stage"]: c["count"] for c in columns}),
        "columns": columns,
        "search_term": request.GET.get("q", "").strip(),
        "employees": (
            Employee.objects.filter(active=True).select_related("user").order_by("user__username")
            if can_see_all else []
        ),
        "assigned_to_filter": request.GET.get("assigned_to", ""),
        "capped": qs.count() > 400,
    })


@login_required
def lead_pipeline_report(request):
    """Conversion rates and where leads die, for a team or for one person."""
    emp = getattr(request.user, "employee", None)
    can_see_team = permissions.is_admin_or_manager(request.user)

    scope = request.GET.get("scope", "team" if can_see_team else "mine")
    qs = _lead_queryset_for_request(request)
    if scope == "mine" or not can_see_team:
        scope = "mine"
        if not emp:
            return HttpResponseForbidden()
        qs = qs.filter(Lead.team_q(emp))

    stages = lead_service.funnel(qs)
    total = qs.count()
    won = qs.filter(is_discarded=False, stage=Lead.STAGE_ORDER).count()
    lost = qs.filter(is_discarded=True).count()

    per_employee = []
    if can_see_team and scope == "team":
        rows = (
            qs.values("assigned_to_id", "assigned_to__user__username")
            .order_by("assigned_to__user__username")
            .annotate(
                total=Count("id"),
                won=Count("id", filter=Q(stage=Lead.STAGE_ORDER, is_discarded=False)),
                lost=Count("id", filter=Q(is_discarded=True)),
            )
        )
        sales_map = {
            row["employee_id"]: row["total_sales"] or 0
            for row in Sale.objects.filter(status=Sale.STATUS_APPROVED)
            .values("employee_id").annotate(total_sales=Sum("amount"))
        }
        for row in rows:
            row["win_pct"] = round(row["won"] / row["total"] * 100, 1) if row["total"] else 0
            row["total_sales"] = sales_map.get(row["assigned_to_id"], 0)
            per_employee.append(row)

    # Leads that have not moved in a fortnight: the actual daily to-do list.
    stale_cutoff = timezone.now() - timedelta(days=14)
    stalled = list(
        qs.filter(is_discarded=False, stage_changed_at__lt=stale_cutoff)
        .exclude(stage=Lead.STAGE_ORDER)
        .order_by("stage_changed_at")[:20]
    )

    # What the pipeline wants, counted from the leads' own product interests.
    demand = (
        Product.objects.filter(lead_interests__lead__in=qs.values("id"), lead_interests__lead__is_discarded=False)
        .annotate(leads=Count("lead_interests", distinct=True), value=Sum("lead_interests__amount"))
        .order_by("-leads")
    )

    return render(request, "clients/leads/lead_pipeline_report.html", {
        "crumbs": [
            {"label": "Leads", "url": reverse("clients:lead_management")},
            {"label": "Pipeline Report"},
        ],
        "kpis": [
            {"label": "Leads", "value": total, "color": "#6B5D3F"},
            {"label": "Won (Order)", "value": won, "color": "#15803D"},
            {"label": "Lost", "value": lost, "color": "#BE123C"},
            {"label": "Win rate", "value": f"{round(won / total * 100, 1) if total else 0}%", "color": "#0369A1"},
        ],
        "scope": scope,
        "can_see_team": can_see_team,
        "stages": stages,
        "per_employee": per_employee,
        "stalled": stalled,
        "demand": demand,
    })


@login_required
def lead_detail(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)

    # The stepper: every SPANCO step with where this lead has got to.
    current = lead.stage_index
    steps = [
        {
            "stage": stage,
            "label": label,
            "help": Lead.STAGE_HELP[stage],
            "color": Lead.STAGE_COLORS[stage],
            "done": index < current,
            "current": index == current,
        }
        for index, (stage, label) in enumerate(Lead.STAGE_CHOICES)
    ]

    return render(request, "clients/leads/lead_detail.html", {
        "crumbs": [
            {"label": "Leads", "url": reverse("clients:lead_management")},
            {"label": lead.customer_name},
        ],
        "lead": lead,
        "steps": steps,
        "stages": Lead.STAGE_CHOICES,
        "next_stage": lead.next_stage,
        "next_stage_label": dict(Lead.STAGE_CHOICES).get(lead.next_stage, ""),
        "interests": lead.interests.select_related("product"),
        "followups": followups.for_source(followups.LEAD, lead.pk),
        "open_task_statuses": Task.OPEN_STATUSES,
        "remarks": lead.remarks.select_related("created_by").order_by("-created_at"),
        "stage_events": lead.stage_events.select_related("created_by")[:50],
    })


@login_required
@require_POST
def lead_set_stage(request, lead_id):
    """Advance (or correct) a lead's SPANCO stage."""
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    stage = (request.POST.get("stage") or "").strip()
    note = (request.POST.get("note") or "").strip()
    try:
        event = lead_service.set_stage(lead, stage, user=request.user, note=note)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        if event:
            messages.success(request, f"{lead.customer_name} moved to {event.to_label}.")
    referer = request.META.get("HTTP_REFERER")
    return redirect(referer) if referer else redirect("clients:lead_detail", lead_id=lead.id)


@login_required
@require_POST
def lead_discard(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    lead_service.mark_lost(lead, user=request.user, reason=(request.POST.get("reason") or "").strip())
    messages.info(request, f"{lead.customer_name} marked lost at {lead.get_stage_display()}.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_undiscard(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    lead_service.reopen(lead, user=request.user)
    messages.success(request, "Lead reopened.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_convert_to_client(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    try:
        client = lead_service.convert_to_client(lead, user=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Lead converted to Client #{client.id} ({client.name}).")
    return redirect("clients:lead_detail", lead_id=lead.id)


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

    lead_service.schedule_followup(lead, when_dt, note=note, actor=request.user)
    messages.success(request, "Follow-up added — it will ring on the phone as a task.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


@login_required
@require_POST
def lead_add_remark(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)
    text = (request.POST.get("text") or "").strip()
    if not text:
        messages.error(request, "Remark cannot be empty.")
        return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))
    lead_service.add_remark(lead, text, user=request.user)
    messages.success(request, "Remark added.")
    return redirect(request.META.get("HTTP_REFERER", "clients:lead_management"))


# Marking a follow-up done and rescheduling one are task actions now
# (clients:task_set_status / clients:task_reschedule) — a follow-up IS a task,
# so it is closed wherever every other task is closed.


@login_required
def lead_create(request):
    initial_lead = Lead()
    is_plain_employee = (
        hasattr(request.user, "employee") and getattr(request.user.employee, "role", "") == "employee"
    )
    if is_plain_employee:
        initial_lead.assigned_to = request.user.employee

    if request.method == "POST":
        form = LeadForm(request.POST, instance=initial_lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(request.POST, instance=initial_lead, prefix="family")
        interest_formset = LeadInterestFormSet(request.POST, instance=initial_lead, prefix="interest")

        if form.is_valid() and family_formset.is_valid() and interest_formset.is_valid():
            with transaction.atomic():
                lead = form.save(commit=False)
                if is_plain_employee:
                    lead.assigned_to = request.user.employee
                lead.created_by = request.user
                lead.stage_changed_at = timezone.now()
                lead.save()
                form.save_m2m()          # collaborators — commit=False skips it

                family_formset.instance = lead
                interest_formset.instance = lead
                family_formset.save()
                interest_formset.save()

                # The lead enters the pipeline: log where it started.
                lead.stage_events.create(
                    to_stage=lead.stage, note="Lead created", created_by=request.user,
                )

            messages.success(request, f"Lead created at {lead.get_stage_display()}.")
            return redirect("clients:lead_detail", lead_id=lead.id)
    else:
        form = LeadForm(instance=initial_lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(instance=initial_lead, prefix="family")
        interest_formset = LeadInterestFormSet(instance=initial_lead, prefix="interest")

    return render(request, "clients/leads/lead_form.html", {
        "form": form,
        "family_formset": family_formset,
        "interest_formset": interest_formset,
        "mode": "create",
        "stage_help": Lead.STAGE_HELP,
    })


@login_required
def lead_update(request, lead_id):
    lead = get_object_or_404(_lead_queryset_for_request(request), pk=lead_id)

    if request.method == "POST":
        form = LeadForm(request.POST, instance=lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(request.POST, instance=lead, prefix="family")
        interest_formset = LeadInterestFormSet(request.POST, instance=lead, prefix="interest")

        if form.is_valid() and family_formset.is_valid() and interest_formset.is_valid():
            with transaction.atomic():
                lead = form.save(commit=False)
                if hasattr(request.user, "employee") and getattr(request.user.employee, "role", "") == "employee":
                    lead.assigned_to = request.user.employee
                lead.save()
                form.save_m2m()          # collaborators — commit=False skips it
                family_formset.save()
                interest_formset.save()
            messages.success(request, "Lead updated successfully.")
            return redirect("clients:lead_detail", lead_id=lead.id)
    else:
        form = LeadForm(instance=lead, user=request.user)
        family_formset = LeadFamilyMemberFormSet(instance=lead, prefix="family")
        interest_formset = LeadInterestFormSet(instance=lead, prefix="interest")

    return render(request, "clients/leads/lead_form.html", {
        "form": form,
        "family_formset": family_formset,
        "interest_formset": interest_formset,
        "lead": lead,
        "mode": "update",
        "stage_help": Lead.STAGE_HELP,
    })


@login_required
def lead_bulk_import(request):
    """Paste a list of names in; they land as Suspects (or a stage you pick).

    Product interests are deliberately not importable — they differ per lead,
    which is the whole reason the fixed Health/Life/Wealth columns went.
    """
    emp = getattr(request.user, "employee", None)
    if not (request.user.is_superuser or emp):
        return HttpResponseForbidden()

    is_admin_or_manager = permissions.is_admin_or_manager(request.user)
    active_employees = list(
        Employee.objects.filter(active=True).select_related("user").order_by("user__username")
    )

    if request.method == "POST":
        try:
            rows = json.loads(request.body).get("rows", [])
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"created": 0, "errors": ["Invalid request body."]}, status=400)

        if not rows:
            return JsonResponse({"created": 0, "errors": ["No rows submitted."]})

        employee_map = {e.id: e for e in active_employees}
        valid_stages = dict(Lead.STAGE_CHOICES)
        created = 0
        errors = []

        for idx, row in enumerate(rows, start=1):
            try:
                customer_name = (row.get("customer_name") or "").strip()
                if not customer_name:
                    raise ValueError("Customer name is required")

                emp_obj = emp
                if is_admin_or_manager and row.get("assigned_to"):
                    try:
                        emp_obj = employee_map[int(row["assigned_to"])]
                    except (ValueError, TypeError, KeyError):
                        raise ValueError(f"Employee id {row.get('assigned_to')} not found")
                if not emp_obj:
                    raise ValueError("Cannot determine assigned employee")

                stage_val = (row.get("stage") or Lead.STAGE_SUSPECT).strip()
                if stage_val not in valid_stages:
                    stage_val = Lead.STAGE_SUSPECT

                with transaction.atomic():
                    lead = Lead.objects.create(
                        customer_name=customer_name,
                        phone=(row.get("phone") or "").strip(),
                        email=(row.get("email") or "").strip(),
                        data_received=bool(row.get("data_received")),
                        notes=(row.get("notes") or "").strip(),
                        assigned_to=emp_obj,
                        created_by=request.user,
                        stage=stage_val,
                    )
                    lead.stage_events.create(
                        to_stage=stage_val, note="Bulk import", created_by=request.user,
                    )
                created += 1
            except Exception as exc:                       # noqa: BLE001 — reported per row
                errors.append(f"Row {idx}: {exc}")

        return JsonResponse({"created": created, "errors": errors})

    return render(request, "clients/leads/lead_bulk_import.html", {
        "is_admin_or_manager": is_admin_or_manager,
        "employees": active_employees,
        "stages": Lead.STAGE_CHOICES,
    })

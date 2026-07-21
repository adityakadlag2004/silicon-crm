"""Insurance Tracker, Claim Tracker and Meetings — list + detail screens.

Read screens only: records are created and edited through the Django admin,
which already gives full CRUD for free. Bespoke forms can come later if the
admin proves too clunky for daily use.
"""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from ..models import InsuranceClaim, InsurancePolicy, Meeting
from ..templatetags.custom_filters import inr


# ─────────────────────────── insurance policies ───────────────────────────

@login_required
def policy_list(request):
    policies = InsurancePolicy.objects.select_related("client", "relationship_manager__user")

    q = (request.GET.get("q") or "").strip()
    if q:
        policies = policies.filter(
            Q(policy_number__icontains=q) | Q(client__name__icontains=q)
            | Q(insurer__icontains=q) | Q(plan_name__icontains=q)
        )
    status = request.GET.get("status", "")
    if status in dict(InsurancePolicy.STATUS_CHOICES):
        policies = policies.filter(status=status)
    kind = request.GET.get("type", "")
    if kind in dict(InsurancePolicy.TYPE_CHOICES):
        policies = policies.filter(insurance_type=kind)

    today = timezone.localdate()
    soon = today + timedelta(days=30)

    agg = InsurancePolicy.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
        lapsed=Count("id", filter=Q(status=InsurancePolicy.STATUS_LAPSED)),
        expiring=Count("id", filter=Q(status=InsurancePolicy.STATUS_ACTIVE,
                                      end_date__gte=today, end_date__lte=soon)),
        cover=Sum("sum_insured", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
    )
    base = reverse("clients:policy_list")
    return render(request, "insurance/policy_list.html", {
        "crumbs": [{"label": "Insurance Tracker"}],
        "kpis": [
            {"label": "All Policies", "value": agg["total"], "color": "#4338CA",
             "url": base, "active": not status},
            {"label": "Active", "value": agg["active"], "color": "#15803D",
             "url": f"{base}?status={InsurancePolicy.STATUS_ACTIVE}",
             "active": status == InsurancePolicy.STATUS_ACTIVE},
            {"label": "Expiring ≤30d", "value": agg["expiring"], "color": "#B45309",
             "url": f"{base}?expiring=1", "active": request.GET.get("expiring") == "1"},
            {"label": "Lapsed", "value": agg["lapsed"], "color": "#BE123C",
             "url": f"{base}?status={InsurancePolicy.STATUS_LAPSED}",
             "active": status == InsurancePolicy.STATUS_LAPSED},
            {"label": "Active Cover", "value": f"₹{inr(agg['cover'] or 0)}", "color": "#0F766E"},
        ],
        # The expiring tile is a date window, not a status — applied last so it
        # composes with whatever else is filtered.
        "policies": (policies.filter(status=InsurancePolicy.STATUS_ACTIVE,
                                     end_date__gte=today, end_date__lte=soon)
                     if request.GET.get("expiring") == "1" else policies)[:300],
        "q": q, "status": status, "kind": kind,
        "statuses": InsurancePolicy.STATUS_CHOICES,
        "types": InsurancePolicy.TYPE_CHOICES,
    })


@login_required
def policy_detail(request, policy_id):
    policy = get_object_or_404(
        InsurancePolicy.objects.select_related("client", "relationship_manager__user"),
        pk=policy_id)
    claims = policy.claims.select_related("handled_by__user")
    # Every renewal logged against this policy — its collection history.
    renewals = policy.renewals.select_related("employee__user").order_by("-premium_collected_on")
    renewals_total = sum((r.premium_amount or 0) for r in renewals)
    return render(request, "insurance/policy_detail.html", {
        "crumbs": [
            {"label": "Insurance Tracker", "url": reverse("clients:policy_list")},
            {"label": policy.policy_number},
        ],
        "kpis": [
            {"label": "Sum Insured", "value": f"₹{inr(policy.sum_insured)}", "color": "#15803D"},
            {"label": "Premium", "value": f"₹{inr(policy.premium_amount)}", "color": "#B45309"},
            {"label": "Renewals", "value": renewals.count(), "color": "#0369A1",
             "sub": f"₹{inr(renewals_total)} collected"},
            {"label": "Claims", "value": claims.count(), "color": "#BE123C"},
        ],
        "policy": policy, "claims": claims,
        "renewals": renewals, "renewals_total": renewals_total,
    })


# ───────────────────────────── claims ─────────────────────────────

@login_required
def claim_list(request):
    claims = InsuranceClaim.objects.select_related(
        "policy__client", "handled_by__user")

    q = (request.GET.get("q") or "").strip()
    if q:
        claims = claims.filter(
            Q(policy__policy_number__icontains=q) | Q(policy__client__name__icontains=q)
            | Q(claim_type__icontains=q))
    status = request.GET.get("status", "")
    if status == "open":
        claims = claims.filter(status__in=InsuranceClaim.OPEN_STATUSES)
    elif status in dict(InsuranceClaim.STATUS_CHOICES):
        claims = claims.filter(status=status)

    agg = InsuranceClaim.objects.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status__in=InsuranceClaim.OPEN_STATUSES)),
        settled=Count("id", filter=Q(status=InsuranceClaim.STATUS_SETTLED)),
        rejected=Count("id", filter=Q(status=InsuranceClaim.STATUS_REJECTED)),
        paid=Sum("settled_amount", filter=Q(status=InsuranceClaim.STATUS_SETTLED)),
    )
    base = reverse("clients:claim_list")
    return render(request, "insurance/claim_list.html", {
        "crumbs": [{"label": "Claim Tracker"}],
        "kpis": [
            {"label": "All Claims", "value": agg["total"], "color": "#4338CA",
             "url": base, "active": not status},
            {"label": "Open", "value": agg["open"], "color": "#B45309",
             "url": f"{base}?status=open", "active": status == "open"},
            {"label": "Settled", "value": agg["settled"], "color": "#15803D",
             "url": f"{base}?status={InsuranceClaim.STATUS_SETTLED}",
             "active": status == InsuranceClaim.STATUS_SETTLED},
            {"label": "Rejected", "value": agg["rejected"], "color": "#BE123C",
             "url": f"{base}?status={InsuranceClaim.STATUS_REJECTED}",
             "active": status == InsuranceClaim.STATUS_REJECTED},
            {"label": "Settled Value", "value": f"₹{inr(agg['paid'] or 0)}", "color": "#0F766E"},
        ],
        "claims": claims[:300], "q": q, "status": status,
        "statuses": InsuranceClaim.STATUS_CHOICES,
    })


@login_required
def claim_detail(request, claim_id):
    claim = get_object_or_404(
        InsuranceClaim.objects.select_related("policy__client", "handled_by__user"),
        pk=claim_id)
    return render(request, "insurance/claim_detail.html", {
        "crumbs": [
            {"label": "Claim Tracker", "url": reverse("clients:claim_list")},
            {"label": f"Claim #{claim.pk}"},
        ],
        "kpis": [
            {"label": "Claimed", "value": f"₹{inr(claim.claimed_amount)}", "color": "#4338CA"},
            {"label": "Settled", "value": f"₹{inr(claim.settled_amount)}", "color": "#15803D"},
            {"label": "Shortfall", "value": f"₹{inr(claim.shortfall)}", "color": "#BE123C"},
        ],
        "claim": claim,
    })


# ───────────────────────────── meetings ─────────────────────────────

@login_required
def meeting_list(request):
    meetings = Meeting.objects.select_related("client", "employee__user")

    q = (request.GET.get("q") or "").strip()
    if q:
        meetings = meetings.filter(Q(client__name__icontains=q) | Q(outcome__icontains=q))
    status = request.GET.get("status", "")
    now = timezone.now()
    if status == "overdue":
        meetings = meetings.filter(status=Meeting.STATUS_SCHEDULED, scheduled_at__lt=now)
    elif status in dict(Meeting.STATUS_CHOICES):
        meetings = meetings.filter(status=status)

    agg = Meeting.objects.aggregate(
        total=Count("id"),
        scheduled=Count("id", filter=Q(status=Meeting.STATUS_SCHEDULED)),
        overdue=Count("id", filter=Q(status=Meeting.STATUS_SCHEDULED, scheduled_at__lt=now)),
        completed=Count("id", filter=Q(status=Meeting.STATUS_COMPLETED)),
    )
    base = reverse("clients:meeting_list")
    return render(request, "insurance/meeting_list.html", {
        "crumbs": [{"label": "Meetings"}],
        "kpis": [
            {"label": "All Meetings", "value": agg["total"], "color": "#4338CA",
             "url": base, "active": not status},
            {"label": "Scheduled", "value": agg["scheduled"], "color": "#0369A1",
             "url": f"{base}?status={Meeting.STATUS_SCHEDULED}",
             "active": status == Meeting.STATUS_SCHEDULED},
            {"label": "Overdue", "value": agg["overdue"], "color": "#BE123C",
             "url": f"{base}?status=overdue", "active": status == "overdue"},
            {"label": "Completed", "value": agg["completed"], "color": "#15803D",
             "url": f"{base}?status={Meeting.STATUS_COMPLETED}",
             "active": status == Meeting.STATUS_COMPLETED},
        ],
        "meetings": meetings[:300], "q": q, "status": status,
        "next_by_rm": _next_meeting_by_rm(),
    })


def _next_meeting_by_rm():
    """RM → count of clients with a next-meeting date booked, by month.

    Mirrors the reference CRM's "RM wise Next Meeting Chart": who has their
    forward book filled and who doesn't.
    """
    rows = (
        Meeting.objects.filter(next_meeting_date__isnull=False)
        .values("employee__user__first_name", "employee__user__username")
        .annotate(n=Count("id")).order_by("-n")
    )
    out = []
    for r in rows:
        name = r["employee__user__first_name"] or r["employee__user__username"] or "Unassigned"
        out.append({"name": name, "count": r["n"]})
    return out


@login_required
@require_GET
def client_policies_json(request, client_id):
    """Health/Life policies already on the tracker for a client.

    The add-renewal screen shows these once a client is picked, so the user
    knows whether they're renewing a known policy or logging a new one.
    """
    from ..models import Client
    from ..services import insurance_sync
    client = get_object_or_404(Client, pk=client_id)
    policies = insurance_sync.client_health_life_policies(client)
    return JsonResponse({
        "policies": [
            {
                "id": p.id,
                "type": p.get_insurance_type_display(),
                "insurer": p.insurer or "—",
                "plan": p.plan_name or "",
                "number": p.policy_number,
                "premium": float(p.premium_amount or 0),
                "start": p.start_date.isoformat() if p.start_date else None,
                "end": p.end_date.isoformat() if p.end_date else None,
                "url": reverse("clients:policy_detail", args=[p.id]),
            }
            for p in policies
        ],
    })

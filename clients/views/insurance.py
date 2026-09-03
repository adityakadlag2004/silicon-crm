"""Insurance Tracker and Claim Tracker — list + detail screens.

Read screens only: records are created and edited through the Django admin,
which already gives full CRUD for free. Bespoke forms can come later if the
admin proves too clunky for daily use.
"""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db.models import Case, Count, IntegerField, Q, Sum, When
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from ..models import InsuranceClaim, InsurancePolicy
from .helpers import name_words_q
from ..templatetags.custom_filters import inr


# ─────────────────────────── insurance policies ───────────────────────────

@login_required
def policy_list(request):
    """The tracker, read one product line at a time.

    Health and Life are different books with different renewal rhythms, so the
    type tabs are the primary control and everything below them — the KPI
    strip included — is scoped to the selected type. A single undivided list
    of every policy was the complaint: it answered no question anybody asks.
    """
    policies = (
        InsurancePolicy.objects
        .select_related("client", "relationship_manager__user")
        .annotate(renewal_count=Count("renewals"))
    )

    kind = request.GET.get("type", "")
    if kind not in dict(InsurancePolicy.TYPE_CHOICES):
        kind = ""
    # Everything below the tabs reads this book only.
    book = policies.filter(insurance_type=kind) if kind else policies

    q = (request.GET.get("q") or "").strip()
    rows = book
    if q:
        rows = rows.filter(
            Q(policy_number__icontains=q) | name_words_q("client__name", q)
            | Q(insurer__icontains=q) | Q(plan_name__icontains=q)
        )
    status = request.GET.get("status", "")
    if status in dict(InsurancePolicy.STATUS_CHOICES):
        rows = rows.filter(status=status)

    today = timezone.localdate()
    soon = today + timedelta(days=30)
    expiring = request.GET.get("expiring") == "1"
    if expiring:
        # A date window, not a status — applied last so it composes with the rest.
        rows = rows.filter(status=InsurancePolicy.STATUS_ACTIVE,
                           end_date__gte=today, end_date__lte=soon)

    # Soonest renewal first: the tracker is a work queue, not an archive. The
    # model's default ordering is newest-expiry-first, which buries the row
    # that needs a call today. Live policies lead — sorting on end_date alone
    # floats every lapsed policy to the top, since their dates are all past.
    rows = rows.annotate(
        _live=Case(When(status=InsurancePolicy.STATUS_ACTIVE, then=0),
                   default=1, output_field=IntegerField()),
    ).order_by("_live", "end_date", "policy_number")

    agg = book.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
        lapsed=Count("id", filter=Q(status=InsurancePolicy.STATUS_LAPSED)),
        expiring=Count("id", filter=Q(status=InsurancePolicy.STATUS_ACTIVE,
                                      end_date__gte=today, end_date__lte=soon)),
        cover=Sum("sum_insured", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
        premium=Sum("premium_amount", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
    )

    base = reverse("clients:policy_list")
    # One count query for every tab, so the tabs themselves say where the book
    # is. Counted off a CLEAN queryset: `policies` carries a Count("renewals")
    # join, and grouping over that counts joined rows — a policy with three
    # renewals lands in its tab three times.
    per_type = {r["insurance_type"]: r["n"] for r in
                InsurancePolicy.objects.values("insurance_type").annotate(n=Count("id"))}
    tabs = [{"key": "", "label": "All", "count": sum(per_type.values()),
             "url": base, "active": not kind}]
    for key, label in InsurancePolicy.TYPE_CHOICES:
        tabs.append({"key": key, "label": label, "count": per_type.get(key, 0),
                     "url": f"{base}?type={key}", "active": kind == key})

    def tab_url(**params):
        """Keep the selected type when a KPI tile or status filter is clicked."""
        parts = [f"type={kind}"] if kind else []
        parts += [f"{k}={v}" for k, v in params.items()]
        return f"{base}?{'&'.join(parts)}" if parts else base

    return render(request, "insurance/policy_list.html", {
        "crumbs": [{"label": "Insurance Tracker"}],
        "kpis": [
            {"label": "Policies", "value": agg["total"], "color": "#4338CA",
             "url": tab_url(), "active": not status and not expiring},
            {"label": "Active", "value": agg["active"], "color": "#15803D",
             "url": tab_url(status=InsurancePolicy.STATUS_ACTIVE),
             "active": status == InsurancePolicy.STATUS_ACTIVE},
            {"label": "Renewing ≤30d", "value": agg["expiring"], "color": "#B45309",
             "url": tab_url(expiring=1), "active": expiring},
            {"label": "Lapsed", "value": agg["lapsed"], "color": "#BE123C",
             "url": tab_url(status=InsurancePolicy.STATUS_LAPSED),
             "active": status == InsurancePolicy.STATUS_LAPSED},
            {"label": "Active Cover", "value": f"₹{inr(agg['cover'] or 0)}", "color": "#0F766E",
             "sub": f"₹{inr(agg['premium'] or 0)} premium"},
        ],
        "policies": rows[:300],
        "q": q, "status": status, "kind": kind,
        "tabs": tabs, "today": today,
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
            Q(policy__policy_number__icontains=q) | name_words_q("policy__client__name", q)
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
    ctx_extra = {
        "activities": claim.activities.select_related("actor")[:100],
        "documents": claim.documents.select_related("uploaded_by"),
        "reminders": claims_service.reminders(claim),
        "open_task_statuses": Task.OPEN_STATUSES,
        "doc_kinds": ClaimDocument.KIND_CHOICES,
        "statuses": InsuranceClaim.STATUS_CHOICES,
        # ordered stage list for the stepper; rejected shown separately
        "stages": [InsuranceClaim.STATUS_INTIMATED, InsuranceClaim.STATUS_FILE_RECEIVED,
                   InsuranceClaim.STATUS_SUBMITTED, InsuranceClaim.STATUS_SETTLED],
    }
    return render(request, "insurance/claim_detail.html", {
        "crumbs": [
            {"label": "Claim Tracker", "url": reverse("clients:claim_list")},
            {"label": f"Claim #{claim.pk}"},
        ],
        **ctx_extra,
        "kpis": [
            {"label": "Claimed", "value": f"₹{inr(claim.claimed_amount)}", "color": "#4338CA"},
            {"label": "Settled", "value": f"₹{inr(claim.settled_amount)}", "color": "#15803D"},
            {"label": "Shortfall", "value": f"₹{inr(claim.shortfall)}", "color": "#BE123C"},
        ],
        "claim": claim,
    })


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


# ─────────────────────────── claim workflow ───────────────────────────

from datetime import datetime as _dt
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from ..forms import ClaimForm
from ..models import ClaimDocument, Task
from ..services import claims as claims_service


def _emp(request):
    return getattr(request.user, "employee", None)


@login_required
def raise_claim(request, policy_id=None):
    """Raise a new claim. Reached from a policy (policy pre-filled) or from the
    nav, where a policy is picked first."""
    policy = get_object_or_404(InsurancePolicy.objects.select_related("client"),
                               pk=policy_id) if policy_id else None

    if request.method == "POST":
        if policy is None:
            pid = request.POST.get("policy")
            policy = get_object_or_404(InsurancePolicy, pk=pid) if pid else None
            if policy is None:
                messages.error(request, "Choose the policy this claim is against.")
                return redirect("clients:claim_list")
        form = ClaimForm(request.POST)
        if form.is_valid():
            claim = form.save(commit=False)
            claim.policy = policy
            claim.created_by = request.user
            if not claim.handled_by_id:
                claim.handled_by = _emp(request)
            if not claim.intimation_date:
                claim.intimation_date = timezone.localdate()
            claim.save()
            claims_service.log(claim, request.user, "created",
                               f"{claim.claim_type or 'Claim'} on {policy.policy_number}")
            messages.success(request, "Claim raised.")
            return redirect("clients:claim_detail", claim_id=claim.pk)
    else:
        form = ClaimForm(initial={"intimation_date": timezone.localdate(),
                                  "status": InsuranceClaim.STATUS_INTIMATED})

    # Policies to choose from when raising without a pre-set one. No cap — the
    # list is searchable, and a truncated list silently hides real policies.
    policies = None
    if policy is None:
        policies = (InsurancePolicy.objects.select_related("client")
                    .order_by("client__name"))
    return render(request, "insurance/claim_form.html", {
        "crumbs": [{"label": "Claim Tracker", "url": reverse("clients:claim_list")},
                   {"label": "Raise Claim"}],
        "form": form, "policy": policy, "policies": policies,
        "statuses": InsuranceClaim.STATUS_CHOICES,
    })


@login_required
@require_POST
def claim_update_status(request, claim_id):
    claim = get_object_or_404(InsuranceClaim.objects.select_related("policy__client"), pk=claim_id)
    new_status = request.POST.get("status")
    settled_raw = (request.POST.get("settled_amount") or "").strip()
    settled = None
    if new_status == InsuranceClaim.STATUS_SETTLED and settled_raw:
        try:
            settled = float(settled_raw)
        except ValueError:
            settled = None
    claims_service.advance_stage(claim, new_status, request.user,
                                 settled_amount=settled,
                                 note=(request.POST.get("note") or "").strip())
    # Optional follow-up reminder attached to this update.
    _maybe_add_reminder(request, claim)
    messages.success(request, "Claim updated.")
    return redirect("clients:claim_detail", claim_id=claim.pk)


@login_required
@require_POST
def claim_add_note(request, claim_id):
    claim = get_object_or_404(InsuranceClaim, pk=claim_id)
    claims_service.add_note(claim, request.user, request.POST.get("note"))
    _maybe_add_reminder(request, claim)
    return redirect("clients:claim_detail", claim_id=claim.pk)


def _maybe_add_reminder(request, claim):
    """If the form carried a follow-up date, schedule a reminder for it."""
    raw = (request.POST.get("reminder_at") or "").strip()
    if not raw:
        return
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = _dt.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        return
    aware = timezone.make_aware(dt, timezone.get_current_timezone())
    claims_service.create_reminder(claim, request.user, aware,
                                   note=request.POST.get("reminder_note", ""))


@login_required
@require_POST
def claim_add_reminder(request, claim_id):
    claim = get_object_or_404(InsuranceClaim, pk=claim_id)
    _maybe_add_reminder(request, claim)
    messages.success(request, "Follow-up scheduled.")
    return redirect("clients:claim_detail", claim_id=claim.pk)


# Closing a claim follow-up is a task action now (clients:task_set_status) —
# a follow-up IS a task, so it closes wherever every other task does.


@login_required
@require_POST
def claim_upload_document(request, claim_id):
    claim = get_object_or_404(InsuranceClaim.objects.select_related("policy__client"), pk=claim_id)
    f = request.FILES.get("document")
    if not f:
        messages.error(request, "Choose a file to upload.")
        return redirect("clients:claim_detail", claim_id=claim.pk)
    _doc, err = claims_service.upload_document(
        claim, f, request.user, kind=request.POST.get("kind", "other"))
    messages.error(request, err) if err else messages.success(request, "Document uploaded.")
    return redirect("clients:claim_detail", claim_id=claim.pk)


@login_required
def claim_document_download(request, doc_id):
    doc = get_object_or_404(ClaimDocument.objects.select_related("claim"), pk=doc_id)
    from ..services.google_drive import stream_file, DriveNotConfigured
    try:
        data, mime = stream_file(doc.drive_file_id)
    except DriveNotConfigured:
        messages.error(request, "Google Drive is not configured.")
        return redirect("clients:claim_detail", claim_id=doc.claim_id)
    except Exception:
        messages.error(request, "Could not fetch the document.")
        return redirect("clients:claim_detail", claim_id=doc.claim_id)
    resp = HttpResponse(data, content_type=mime or doc.mime or "application/octet-stream")
    resp["Content-Disposition"] = f'inline; filename="{doc.filename}"'
    return resp


@login_required
@require_POST
def claim_delete_document(request, doc_id):
    doc = get_object_or_404(ClaimDocument.objects.select_related("claim"), pk=doc_id)
    claim_id = doc.claim_id
    from ..services.google_drive import delete_file
    try:
        delete_file(doc.drive_file_id)
    except Exception:
        pass
    claims_service.log(doc.claim, request.user, "document_removed", doc.filename)
    doc.delete()
    messages.success(request, "Document removed.")
    return redirect("clients:claim_detail", claim_id=claim_id)

"""JSON API for the app's Insurance module — policies, claims, documents.

A claim is intimated over the phone, from wherever the client is, and its
papers are photographed on the spot. The whole workflow lived on the web only,
which is the one place nobody is standing when it happens.

Everything routes through `services/claims.py`, exactly like the web views, so
the activity trail and the stage dates stay identical whichever screen made
the change. Visibility matches the web screens: any signed-in user sees the
tracker (the firm's standing decision on client visibility).
"""
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import ClaimDocument, InsuranceClaim, InsurancePolicy, Task
from ..services import claims as claims_service
from .helpers import name_words_q

_PAGE = 25


def _emp(request):
    return getattr(request.user, "employee", None)


def _money(value):
    return float(value or 0)


def _date(value):
    return value.isoformat() if value else None


def _parse_date(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_amount(raw):
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception:
        return {}


@login_required
@require_GET
def app_insurance_meta(request):
    """Every picker the two screens need, served rather than hard-coded — the
    stage list is the workflow, and the app must not carry its own copy."""
    return JsonResponse({
        "policy_statuses": [{"value": v, "label": l} for v, l in InsurancePolicy.STATUS_CHOICES],
        "policy_types": [{"value": v, "label": l} for v, l in InsurancePolicy.TYPE_CHOICES],
        "claim_statuses": [{"value": v, "label": l} for v, l in InsuranceClaim.STATUS_CHOICES],
        # The stepper's spine: Rejected is an exit, not a step, so it is left out.
        "claim_stages": [
            InsuranceClaim.STATUS_INTIMATED, InsuranceClaim.STATUS_FILE_RECEIVED,
            InsuranceClaim.STATUS_SUBMITTED, InsuranceClaim.STATUS_SETTLED,
        ],
        "claim_modes": [{"value": v, "label": l} for v, l in InsuranceClaim.MODE_CHOICES],
        "document_kinds": [{"value": v, "label": l} for v, l in ClaimDocument.KIND_CHOICES],
    })


def _policy_row(p, today):
    return {
        "id": p.id,
        "number": p.policy_number,
        "client": p.client.name if p.client_id else "",
        "client_id": p.client_id,
        "insurer": p.insurer or "",
        "plan": p.plan_name or "",
        "type": p.get_insurance_type_display(),
        "status": p.status,
        "status_label": p.get_status_display(),
        "sum_insured": _money(p.sum_insured),
        "premium": _money(p.premium_amount),
        "start_date": _date(p.start_date),
        "end_date": _date(p.end_date),
        # The number the screen sorts a renewal conversation by.
        "days_to_expiry": (p.end_date - today).days if p.end_date else None,
    }


@login_required
@require_GET
def app_policies(request):
    qs = InsurancePolicy.objects.select_related("client")
    today = timezone.localdate()

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(policy_number__icontains=q) | name_words_q("client__name", q)
            | Q(insurer__icontains=q) | Q(plan_name__icontains=q)
        )
    status = request.GET.get("status", "")
    if status == "expiring":
        qs = qs.filter(status=InsurancePolicy.STATUS_ACTIVE,
                       end_date__gte=today, end_date__lte=today + timedelta(days=30))
    elif status in dict(InsurancePolicy.STATUS_CHOICES):
        qs = qs.filter(status=status)
    kind = request.GET.get("type", "")
    if kind in dict(InsurancePolicy.TYPE_CHOICES):
        qs = qs.filter(insurance_type=kind)

    agg = InsurancePolicy.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
        lapsed=Count("id", filter=Q(status=InsurancePolicy.STATUS_LAPSED)),
        expiring=Count("id", filter=Q(status=InsurancePolicy.STATUS_ACTIVE,
                                      end_date__gte=today,
                                      end_date__lte=today + timedelta(days=30))),
        cover=Sum("sum_insured", filter=Q(status=InsurancePolicy.STATUS_ACTIVE)),
    )

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    rows = list(qs.order_by("-end_date", "policy_number")[(page - 1) * _PAGE:page * _PAGE + 1])

    return JsonResponse({
        "counts": {
            "total": agg["total"], "active": agg["active"],
            "expiring": agg["expiring"], "lapsed": agg["lapsed"],
            "cover": _money(agg["cover"]),
        },
        "page": page,
        "has_more": len(rows) > _PAGE,
        "results": [_policy_row(p, today) for p in rows[:_PAGE]],
    })


@login_required
@require_GET
def app_policy_detail(request, policy_id):
    policy = get_object_or_404(
        InsurancePolicy.objects.select_related("client", "relationship_manager__user"),
        pk=policy_id)
    today = timezone.localdate()
    row = _policy_row(policy, today)
    row.update({
        "client_phone": policy.client.phone if policy.client_id else "",
        "nominee": policy.nominee_name or "",
        "nominee_relationship": policy.nominee_relationship or "",
        "manager": (policy.relationship_manager.user.get_full_name()
                    or policy.relationship_manager.user.username)
        if policy.relationship_manager_id and policy.relationship_manager.user_id else "",
        "notes": policy.notes or "",
        "claims": [
            _claim_row(c) for c in
            policy.claims.select_related("policy__client", "handled_by__user")
        ],
        "renewals": [
            {
                "id": r.pk,
                "date": _date(r.renewal_date),
                "premium": _money(r.premium_amount),
                "collected_on": _date(r.premium_collected_on),
            }
            for r in policy.renewals.order_by("-renewal_date")[:20]
        ],
    })
    return JsonResponse(row)


def _claim_row(c):
    return {
        "id": c.id,
        "policy_id": c.policy_id,
        "policy_number": c.policy.policy_number if c.policy_id else "",
        "client": c.policy.client.name if c.policy_id and c.policy.client_id else "",
        "claim_type": c.claim_type or "Claim",
        "mode": c.get_claim_mode_display(),
        "status": c.status,
        "status_label": c.get_status_display(),
        "is_open": c.status in InsuranceClaim.OPEN_STATUSES,
        "claimed": _money(c.claimed_amount),
        "settled": _money(c.settled_amount),
        "shortfall": _money(c.shortfall),
        "intimation_date": _date(c.intimation_date),
        "handler": (c.handled_by.user.get_full_name() or c.handled_by.user.username)
        if c.handled_by_id and c.handled_by.user_id else "",
    }


@login_required
@require_GET
def app_claims(request):
    qs = InsuranceClaim.objects.select_related("policy__client", "handled_by__user")

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(policy__policy_number__icontains=q) | name_words_q("policy__client__name", q)
            | Q(claim_type__icontains=q))
    status = request.GET.get("status", "")
    if status == "open":
        qs = qs.filter(status__in=InsuranceClaim.OPEN_STATUSES)
    elif status in dict(InsuranceClaim.STATUS_CHOICES):
        qs = qs.filter(status=status)

    agg = InsuranceClaim.objects.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status__in=InsuranceClaim.OPEN_STATUSES)),
        settled=Count("id", filter=Q(status=InsuranceClaim.STATUS_SETTLED)),
        rejected=Count("id", filter=Q(status=InsuranceClaim.STATUS_REJECTED)),
        paid=Sum("settled_amount", filter=Q(status=InsuranceClaim.STATUS_SETTLED)),
    )

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    rows = list(qs[(page - 1) * _PAGE:page * _PAGE + 1])

    return JsonResponse({
        "counts": {
            "total": agg["total"], "open": agg["open"],
            "settled": agg["settled"], "rejected": agg["rejected"],
            "paid": _money(agg["paid"]),
        },
        "page": page,
        "has_more": len(rows) > _PAGE,
        "results": [_claim_row(c) for c in rows[:_PAGE]],
    })


@login_required
@require_GET
def app_claim_detail(request, claim_id):
    claim = get_object_or_404(
        InsuranceClaim.objects.select_related("policy__client", "handled_by__user"),
        pk=claim_id)
    row = _claim_row(claim)
    row.update({
        "client_phone": claim.policy.client.phone if claim.policy.client_id else "",
        "admission_date": _date(claim.admission_date),
        "submission_date": _date(claim.submission_date),
        "settlement_date": _date(claim.settlement_date),
        "settlement_details": claim.settlement_details or "",
        "activities": [
            {
                "action": a.get_action_display(),
                "detail": a.detail,
                "actor": (a.actor.get_full_name() or a.actor.username) if a.actor_id else "System",
                "at": timezone.localtime(a.created_at).strftime("%d %b %Y %H:%M"),
            }
            for a in claim.activities.select_related("actor")[:60]
        ],
        "documents": [
            {
                "id": doc.id,
                "kind": doc.get_kind_display(),
                "filename": doc.filename,
                # The web proxy streams it out of Drive; the app opens it in
                # the WebView rather than re-implementing a viewer.
                "url": f"/clients/claims/document/{doc.id}/download/",
            }
            for doc in claim.documents.all()
        ],
        # A claim follow-up is a Task, so these are tasks.
        "reminders": [
            {
                "id": t.pk,
                "title": t.title,
                "due": _date(t.due_date),
                "status": t.status,
                "open": t.status in Task.OPEN_STATUSES,
            }
            for t in claims_service.reminders(claim)[:20]
        ],
    })
    return JsonResponse(row)


@login_required
@require_POST
def app_claim_create(request):
    """Raise a claim against a policy. Mirrors the web `raise_claim` POST."""
    body = _body(request)
    policy = get_object_or_404(InsurancePolicy, pk=body.get("policy_id") or 0)

    claim = InsuranceClaim(
        policy=policy,
        claim_type=(body.get("claim_type") or "").strip()[:120],
        claim_mode=(body.get("claim_mode") or InsuranceClaim.MODE_CASHLESS),
        status=InsuranceClaim.STATUS_INTIMATED,
        intimation_date=_parse_date(body.get("intimation_date")) or timezone.localdate(),
        admission_date=_parse_date(body.get("admission_date")),
        claimed_amount=_parse_amount(body.get("claimed_amount")) or 0,
        handled_by=_emp(request),
        created_by=request.user,
    )
    if claim.claim_mode not in dict(InsuranceClaim.MODE_CHOICES):
        claim.claim_mode = InsuranceClaim.MODE_CASHLESS
    claim.save()
    claims_service.log(claim, request.user, "created",
                       f"{claim.claim_type or 'Claim'} on {policy.policy_number}")
    note = (body.get("note") or "").strip()
    if note:
        claims_service.add_note(claim, request.user, note)
    return JsonResponse({"ok": True, "id": claim.pk})


@login_required
@require_POST
def app_claim_update(request, claim_id):
    """One submit covers the three things a claim update ever is: a stage move,
    a note, and a follow-up — the same shape as the web update form."""
    claim = get_object_or_404(
        InsuranceClaim.objects.select_related("policy__client"), pk=claim_id)
    body = _body(request)

    status = body.get("status")
    if status:
        settled = (_parse_amount(body.get("settled_amount"))
                   if status == InsuranceClaim.STATUS_SETTLED else None)
        claims_service.advance_stage(claim, status, request.user,
                                     settled_amount=settled,
                                     note=(body.get("note") or "").strip())
    elif (body.get("note") or "").strip():
        claims_service.add_note(claim, request.user, body.get("note"))

    reminder_at = (body.get("reminder_at") or "").strip()
    if reminder_at:
        # "YYYY-MM-DDTHH:MM" from the app's date+time picker.
        parsed = None
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(reminder_at, fmt)
                break
            except ValueError:
                continue
        if parsed is not None:
            claims_service.create_reminder(
                claim, request.user,
                timezone.make_aware(parsed, timezone.get_current_timezone()),
                note=(body.get("reminder_note") or "").strip(),
                employee=claim.handled_by,
            )
    return JsonResponse({"ok": True, "status": claim.status})


@login_required
@require_POST
def app_claim_document_upload(request, claim_id):
    """Multipart upload of one document — the photo of the bill, taken in the
    hospital corridor. `kind` rides in the query string because the multipart
    helper on the device sends the file field and nothing else."""
    claim = get_object_or_404(
        InsuranceClaim.objects.select_related("policy__client"), pk=claim_id)
    f = request.FILES.get("document") or request.FILES.get("attachments")
    if not f:
        return JsonResponse({"ok": False, "error": "No file received."}, status=400)
    kind = request.POST.get("kind") or request.GET.get("kind") or "other"
    if kind not in dict(ClaimDocument.KIND_CHOICES):
        kind = "other"
    doc, err = claims_service.upload_document(claim, f, request.user, kind=kind)
    if err:
        return JsonResponse({"ok": False, "error": err}, status=400)
    return JsonResponse({"ok": True, "id": doc.id, "filename": doc.filename})

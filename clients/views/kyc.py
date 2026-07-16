"""Client KYC Issues — missing PANs and duplicate profiles.

PAN is what links a client to their RTA mutual-fund folios, so clients
without one break auto-linking and sale verification. This screen lists them
(each employee sees their own mapped clients; managers/admins see everyone),
lets PANs be filled inline, and — for admins — surfaces likely duplicate
profiles with merge / safe-delete actions.
"""
import re
from collections import defaultdict

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import validate_pan
from ..models import Client
from ..services import client_merge, rta_feed


def _role(request):
    emp = getattr(request.user, "employee", None)
    return getattr(emp, "role", "") if emp else ""


def _is_admin(request):
    return request.user.is_superuser or _role(request) == "admin"


def _missing_pan_qs(request):
    qs = Client.objects.filter(Q(pan__isnull=True) | Q(pan="")).select_related("mapped_to__user")
    if _role(request) == "employee":
        qs = qs.filter(mapped_to=request.user.employee)
    return qs.order_by("mapped_to__user__username", "name")


def missing_pan_count_for(user):
    """Badge count for dashboards: scope matches what the KYC page shows."""
    emp = getattr(user, "employee", None)
    if not emp:
        return 0
    qs = Client.objects.filter(Q(pan__isnull=True) | Q(pan=""))
    if emp.role == "employee":
        qs = qs.filter(mapped_to=emp)
    return qs.count()


def _duplicate_groups():
    """Likely duplicate profiles, grouped by shared PAN, phone, or exact name."""
    groups = []
    seen_ids = set()

    def add_groups(key_fn, label, clients):
        buckets = defaultdict(list)
        for c in clients:
            key = key_fn(c)
            if key:
                buckets[key].append(c)
        for key, members in buckets.items():
            ids = frozenset(c.id for c in members)
            if len(members) > 1 and ids not in seen_ids:
                seen_ids.add(ids)
                groups.append({"label": label, "key": key, "clients": members})

    clients = list(Client.objects.select_related("mapped_to__user"))
    add_groups(lambda c: re.sub(r"[^A-Z0-9]", "", (c.pan or "").upper()), "Same PAN", clients)
    add_groups(lambda c: re.sub(r"\D", "", c.phone or "")[-10:] or None, "Same phone", clients)
    add_groups(lambda c: (c.name or "").strip().upper() or None, "Same name", clients)

    for group in groups:
        for c in group["clients"]:
            c.record_counts = client_merge.business_record_counts(c)
            c.record_total = sum(c.record_counts.values())
    return groups


@login_required
def client_kyc_issues(request):
    missing = list(_missing_pan_qs(request))
    context = {
        "page_title": "Client KYC Issues",
        "missing": missing,
        "is_admin": _is_admin(request),
        "duplicate_groups": _duplicate_groups() if _is_admin(request) else [],
    }
    return render(request, "clients/kyc_issues.html", context)


@login_required
@require_POST
def client_kyc_update_pan(request, client_id):
    client = get_object_or_404(Client, id=client_id)
    if _role(request) == "employee" and client.mapped_to != request.user.employee:
        return HttpResponseForbidden("You can update only your assigned clients.")
    try:
        pan = validate_pan(request.POST.get("pan"), required=True)
    except forms.ValidationError as exc:
        messages.error(request, f"{client.name}: {'; '.join(exc.messages)}")
        return redirect("clients:client_kyc_issues")

    client.pan = pan
    client.save(update_fields=["pan"])
    linked = rta_feed.relink_folios()
    note = f" — {linked} MF folio(s) auto-linked" if linked else ""
    messages.success(request, f"PAN saved for {client.name}{note}.")
    return redirect("clients:client_kyc_issues")


@login_required
@require_POST
def client_merge_view(request):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    keep = get_object_or_404(Client, id=request.POST.get("keep_id"))
    merged = []
    for remove_id in request.POST.getlist("remove_id"):
        remove = Client.objects.filter(id=remove_id).exclude(id=keep.id).first()
        if remove is None:
            continue
        moved = client_merge.merge_clients(keep, remove)
        summary = ", ".join(f"{n} {label}" for label, n in moved.items()) or "no records"
        merged.append(f"'{remove.name}' (#{remove.id}: {summary})")
    if merged:
        messages.success(request, f"Merged into '{keep.name}' (#{keep.id}): {'; '.join(merged)}.")
    else:
        messages.error(request, "Nothing to merge.")
    return redirect("clients:client_kyc_issues")


@login_required
@require_POST
def client_safe_delete(request, client_id):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    client = get_object_or_404(Client, id=client_id)
    counts = client_merge.business_record_counts(client)
    # Mapping-audit rows are bookkeeping, not business — they never block deletion.
    counts.pop("mapping_audits", None)
    if counts:
        summary = ", ".join(f"{n} {label}" for label, n in counts.items())
        messages.error(
            request,
            f"'{client.name}' has business records ({summary}) — merge it into the correct profile instead of deleting.",
        )
        return redirect("clients:client_kyc_issues")
    name = client.name
    client.delete()
    messages.success(request, f"Deleted empty duplicate profile '{name}'.")
    return redirect("clients:client_kyc_issues")

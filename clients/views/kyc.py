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
from .. import permissions
from ..models import Client
from ..services import client_merge, rta_feed


def _role(request):
    emp = getattr(request.user, "employee", None)
    return getattr(emp, "role", "") if emp else ""


def _is_admin(request):
    return permissions.is_admin(request.user)


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


def _ids_with_business():
    """Client ids that hold any real business record — cheap union used to
    find delete-candidate profiles. client_safe_delete stays the
    authoritative guard (it checks every relation)."""
    from ..models import (CalendarEvent, CallFollowUp, MutualFundFolio,
                          Renewal, Sale, SipRegistration, Task)

    ids = set()
    for model, field in ((Sale, "client_id"), (Renewal, "client_id"),
                         (MutualFundFolio, "client_id"), (SipRegistration, "client_id"),
                         (CallFollowUp, "client_id"), (CalendarEvent, "client_id"),
                         (Task, "client_id")):
        ids |= set(model.objects.exclude(**{f"{field}__isnull": True})
                   .values_list(field, flat=True).distinct())
    return ids


def _hygiene_lists():
    """Junk-name profiles (single word) and profiles with no business data —
    the two lists behind 'clean up my client base'."""
    busy_ids = _ids_with_business()
    single_word = [
        c for c in Client.objects.select_related("mapped_to__user").order_by("name")
        if len((c.name or "").strip().split()) <= 1
    ][:100]
    for c in single_word:
        c.has_business = c.id in busy_ids
    no_business = list(
        Client.objects.exclude(id__in=busy_ids)
        .select_related("mapped_to__user").order_by("name")[:100]
    )
    return single_word, no_business


def _folio_identity_issues():
    """Folio↔client identity conflicts — usually a wrong PAN typed on the
    client or a folio linked to the wrong person:
      - the client's folios carry 2+ different PANs
      - the client's PAN differs from the folio PAN(s)
      - a linked folio's investor name shares no word with the client's name
    """
    from ..models import MutualFundFolio
    from ..services.rta_feed import _name_tokens

    by_client = defaultdict(list)
    for folio in (MutualFundFolio.objects.filter(client__isnull=False)
                  .select_related("client")):
        by_client[folio.client].append(folio)

    issues = []
    for client, folios in by_client.items():
        pans = {f.pan for f in folios if f.pan}
        client_pan = re.sub(r"[^A-Z0-9]", "", (client.pan or "").upper())
        client_tokens = set(_name_tokens(client.name))
        name_mismatch_ids = {
            f.id for f in folios
            if _name_tokens(f.investor_name)
            and client_tokens
            and not (set(_name_tokens(f.investor_name)) & client_tokens)
        }
        problems = []
        if len(pans) > 1:
            problems.append(f"folios carry {len(pans)} different PANs")
        if client_pan and pans and client_pan not in pans:
            problems.append("client PAN matches none of the folio PANs")
        if name_mismatch_ids:
            problems.append("investor name shares no word with the client name")
        if problems:
            for f in folios:
                f.name_mismatch = f.id in name_mismatch_ids
            issues.append({
                "client": client, "folios": folios,
                "pans": sorted(pans), "problems": problems,
            })
    return issues


@login_required
def client_kyc_issues(request):
    from ..models import MutualFundFolio
    from ..services import rta_feed

    missing = list(_missing_pan_qs(request))
    is_admin = _is_admin(request)

    # Folio name-matches suggest PANs for the missing-PAN rows: investor name
    # shown alongside for a visual cross-check before bulk-applying.
    suggestions = rta_feed.suggest_folio_matches()
    best_by_client = {}
    for s in suggestions:
        cid = s["client"].id
        if s["pan"] and (cid not in best_by_client or s["level"] > best_by_client[cid]["level"]):
            best_by_client[cid] = s
    for c in missing:
        c.pan_suggestion = best_by_client.get(c.id)

    context = {
        "page_title": "Client KYC & Data Health",
        "missing": missing,
        "is_admin": is_admin,
        "duplicate_groups": _duplicate_groups() if is_admin else [],
    }
    if is_admin:
        single_word, no_business = _hygiene_lists()
        context.update({
            "single_word": single_word,
            "no_business": no_business,
            "unlinked_folios": MutualFundFolio.objects.filter(client__isnull=True).count(),
            "match_suggestions": len(suggestions),
            "identity_issues": _folio_identity_issues(),
        })
    return render(request, "clients/kyc_issues.html", context)


@login_required
@require_POST
def client_bulk_merge(request):
    """Merge every duplicate group where a keeper was ticked — one click for
    the whole page instead of group-by-group."""
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    merged = []
    try:
        group_count = min(int(request.POST.get("group_count", 0)), 300)
    except ValueError:
        group_count = 0
    for i in range(group_count):
        keep_id = request.POST.get(f"keep_g{i}", "")
        # only the profiles the admin explicitly ticked in this group are
        # merged — unrelated members sharing a phone are left alone
        merge_ids = request.POST.getlist(f"merge_g{i}")
        if not keep_id.isdigit() or not merge_ids:
            continue
        keep = Client.objects.filter(id=keep_id).first()
        if keep is None:
            continue
        for rid in merge_ids:
            if rid == keep_id or not rid.isdigit():
                continue
            remove = Client.objects.filter(id=rid).first()
            if remove is None:  # already merged away via an overlapping group
                continue
            client_merge.merge_clients(keep, remove)
            merged.append(f"'{remove.name}' → '{keep.name}'")
    if merged:
        listing = "; ".join(merged[:8]) + ("…" if len(merged) > 8 else "")
        messages.success(request, f"Merged {len(merged)} profile(s): {listing}")
    else:
        messages.info(request, "No group had a keeper ticked — nothing merged.")
    return redirect("clients:client_kyc_issues")


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

    duplicate = Client.objects.filter(pan__iexact=pan).exclude(id=client.id).first()
    if duplicate:
        messages.error(
            request,
            f"PAN {pan} already belongs to '{duplicate.name}' (#{duplicate.id}) — "
            f"merge the two profiles instead of assigning the same PAN twice.",
        )
        return redirect("clients:client_kyc_issues")

    client.pan = pan
    client.save(update_fields=["pan"])
    linked = rta_feed.relink_folios()
    note = f" — {linked} MF record(s) auto-linked" if linked else ""
    messages.success(request, f"PAN saved for {client.name}{note}.")
    return redirect("clients:client_kyc_issues")


@login_required
@require_POST
def client_merge_view(request):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    keep_id = (request.POST.get("keep_id") or "").strip().lstrip("#")
    keep = Client.objects.filter(id=keep_id).first() if keep_id.isdigit() else None
    if keep is None:
        messages.error(request, f"No client with ID '{request.POST.get('keep_id')}' — check the number and try again.")
        return redirect("clients:client_kyc_issues")
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

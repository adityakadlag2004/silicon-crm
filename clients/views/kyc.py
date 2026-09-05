"""Client KYC Issues — missing PANs and dates of birth, and duplicate profiles.

PAN is the client's identity key, so profiles without one can't be matched or
de-duplicated. This screen lists them (each employee sees their own mapped
clients; managers/admins see everyone), lets PANs be filled inline, and — for
admins — surfaces likely duplicate profiles with merge / safe-delete actions.
"""
import re
from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import validate_pan
from .. import permissions
from ..models import Client
from .helpers import name_words_q
from ..services import client_alerts, client_merge


def _role(request):
    emp = getattr(request.user, "employee", None)
    return getattr(emp, "role", "") if emp else ""


def _is_admin(request):
    return permissions.is_admin(request.user)


MISSING_PAN_PER_PAGE = 100
DUPLICATE_GROUPS_PER_PAGE = 25


def _missing_pan_qs(request, search=""):
    qs = Client.objects.filter(Q(pan__isnull=True) | Q(pan="")).select_related("mapped_to__user")
    if _role(request) == "employee":
        qs = qs.filter(mapped_to=request.user.employee)
    if search:
        qs = qs.filter(name_words_q("name", search)
                       | Q(phone__icontains=search)
                       | Q(mapped_to__user__username__icontains=search))
    return qs.order_by("mapped_to__user__username", "name")


def _missing_dob_qs(request, search=""):
    """Clients with no date of birth — scoped exactly like the missing-PAN list.

    Date of birth became mandatory for new clients in Sep 2026; the imported
    book has none, and their age is what drives the retirement-planning alert
    at 40, so these are worth chasing rather than blocking every edit over.
    """
    qs = Client.objects.filter(date_of_birth__isnull=True).select_related("mapped_to__user")
    if _role(request) == "employee":
        qs = qs.filter(mapped_to=request.user.employee)
    if search:
        qs = qs.filter(name_words_q("name", search)
                       | Q(phone__icontains=search)
                       | Q(mapped_to__user__username__icontains=search))
    return qs.order_by("mapped_to__user__username", "name")


def _kyc_redirect(request, anchor="missing-pan"):
    """Back to the KYC screen on the page/search the row was saved from —
    without this, every save bounced the user to the top of 1800+ rows."""
    url = redirect("clients:client_kyc_issues").url
    keys = (("q", "q"), ("page", "page")) if anchor == "missing-pan" else (("dq", "dq"), ("dpage", "dpage"))
    params = urlencode({k: v for k, v in
                        ((key, request.POST.get(post_key, "").strip()) for key, post_key in keys)
                        if v})
    return redirect(f"{url}?{params}#{anchor}" if params else f"{url}#{anchor}")


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

    # Scan on values, not model instances: building 3k Client objects (wide
    # rows, plus the employee/user join) to read three fields was most of this
    # page. Only the profiles that land in a group get loaded for real.
    rows = list(Client.objects.values_list("id", "pan", "phone", "name"))

    def add_groups(key_fn, label):
        buckets = defaultdict(list)
        for row in rows:
            key = key_fn(row)
            if key:
                buckets[key].append(row[0])
        for key, member_ids in buckets.items():
            ids = frozenset(member_ids)
            if len(member_ids) > 1 and ids not in seen_ids:
                seen_ids.add(ids)
                groups.append({"label": label, "key": key, "ids": member_ids})

    add_groups(lambda r: re.sub(r"[^A-Z0-9]", "", (r[1] or "").upper()), "Same PAN")
    add_groups(lambda r: re.sub(r"\D", "", r[2] or "")[-10:] or None, "Same phone")
    add_groups(lambda r: (r[3] or "").strip().upper() or None, "Same name")

    by_id = Client.objects.select_related("mapped_to__user").in_bulk(
        {cid for group in groups for cid in group["ids"]}
    )
    for group in groups:
        group["clients"] = [by_id[cid] for cid in group.pop("ids") if cid in by_id]

    # One batched pass for every profile on the page — counting per client here
    # meant thousands of queries once the duplicate list grew.
    counts_by_id = client_merge.business_record_counts_bulk(
        {c.id for group in groups for c in group["clients"]}
    )
    for group in groups:
        for c in group["clients"]:
            c.record_counts = counts_by_id.get(c.id, {})
            c.record_total = sum(c.record_counts.values())
    return groups


def _ids_with_business():
    """Client ids that hold any real business record — cheap union used to
    find delete-candidate profiles. client_safe_delete stays the
    authoritative guard (it checks every relation)."""
    from ..models import (CalendarEvent, CallFollowUp, Renewal, Sale, Task)

    ids = set()
    for model, field in ((Sale, "client_id"), (Renewal, "client_id"),
                         (CallFollowUp, "client_id"), (CalendarEvent, "client_id"),
                         (Task, "client_id")):
        ids |= set(model.objects.exclude(**{f"{field}__isnull": True})
                   .values_list(field, flat=True).distinct())
    return ids


def _hygiene_lists():
    """Junk-name profiles (single word) and profiles with no business data —
    the two lists behind 'clean up my client base'."""
    busy_ids = _ids_with_business()
    # "Two or more words" is <non-space><space><non-space>; excluding that in
    # SQL beats loading every client to throw nearly all of them away.
    single_word = list(
        Client.objects.exclude(name__regex=r"\S\s+\S")
        .select_related("mapped_to__user").order_by("name")[:100]
    )
    for c in single_word:
        c.has_business = c.id in busy_ids
    no_business = list(
        Client.objects.exclude(id__in=busy_ids)
        .select_related("mapped_to__user").order_by("name")[:100]
    )
    return single_word, no_business


@login_required
def client_kyc_issues(request):
    is_admin = _is_admin(request)
    search = (request.GET.get("q") or "").strip()

    # Paginated: rendering all ~1800 missing-PAN rows built a 2MB page, and
    # every inline PAN save reloaded the whole thing.
    page_obj = Paginator(_missing_pan_qs(request, search), MISSING_PAN_PER_PAGE) \
        .get_page(request.GET.get("page"))
    missing = list(page_obj)

    # Missing dates of birth carry their own search + page params, so filling in
    # one list never resets the other.
    dob_search = (request.GET.get("dq") or "").strip()
    dob_page = Paginator(_missing_dob_qs(request, dob_search), MISSING_PAN_PER_PAGE) \
        .get_page(request.GET.get("dpage"))

    context = {
        "page_title": "Client KYC & Data Health",
        "kpis": [
            {"label": "Missing PAN", "value": page_obj.paginator.count, "color": "#BE123C"},
            {"label": "Missing DOB", "value": dob_page.paginator.count, "color": "#B45309"},
        ],
        "missing": missing,
        "page_obj": page_obj,
        "missing_total": page_obj.paginator.count,
        "search": search,
        "missing_dob": list(dob_page),
        "dob_page_obj": dob_page,
        "missing_dob_total": dob_page.paginator.count,
        "dob_search": dob_search,
        "today": timezone.localdate().isoformat(),
        "is_admin": is_admin,
    }
    # 149 groups / 306 profiles on production, all rendered at once. Duplicates
    # are worked through a page at a time like every other queue here; the
    # header still reports the true total so the backlog stays visible.
    all_groups = _duplicate_groups() if is_admin else []
    dup_page = Paginator(all_groups, DUPLICATE_GROUPS_PER_PAGE).get_page(
        request.GET.get("gpage"))
    context.update({
        "duplicate_groups": list(dup_page),
        "duplicate_page_obj": dup_page,
        "duplicate_total": len(all_groups),
    })
    if is_admin:
        context.update(_merge_search_context(request))
    if is_admin:
        single_word, no_business = _hygiene_lists()
        context.update({
            "single_word": single_word,
            "no_business": no_business,
        })
    return render(request, "clients/kyc_issues.html", context)


MERGE_SEARCH_LIMIT = 40


def _merge_search_context(request):
    """Clients matching the typed name, for the manual merge picker.

    The automatic duplicate groups only catch an *exact* shared PAN, phone or
    name, so "Rajesh Sharma" and "Sharma Rajesh Kumar" sit there as two
    profiles forever. ``name_words_q`` matches every word in any order, which
    is what somebody actually means when they type a name looking for
    duplicates. Each candidate carries what a merge would move, so the keeper
    is an informed pick rather than a guess.
    """
    query = (request.GET.get("mq") or "").strip()
    if not query:
        return {"merge_query": "", "merge_candidates": []}
    candidates = list(
        Client.objects.filter(name_words_q("name", query))
        .select_related("mapped_to__user")
        .order_by("name", "id")[:MERGE_SEARCH_LIMIT]
    )
    counts = client_merge.business_record_counts_bulk({c.id for c in candidates})
    for c in candidates:
        c.record_counts = counts.get(c.id, {})
        c.record_total = sum(c.record_counts.values())
    return {"merge_query": query, "merge_candidates": candidates}


@login_required
@require_POST
def client_kyc_update_dob(request, client_id):
    """Fill in one old client's date of birth from the KYC screen."""
    client = get_object_or_404(Client, id=client_id)
    if _role(request) == "employee" and client.mapped_to != request.user.employee:
        return HttpResponseForbidden("You can update only your assigned clients.")
    raw = (request.POST.get("date_of_birth") or "").strip()
    try:
        dob = date.fromisoformat(raw)
    except ValueError:
        messages.error(request, f"{client.name}: enter the date of birth as YYYY-MM-DD.")
        return _kyc_redirect(request, "missing-dob")

    # Same two sanity checks the add form applies — a typo'd year would sit in
    # the age reports (and the retirement alert) forever.
    today = timezone.localdate()
    if dob > today:
        messages.error(request, f"{client.name}: date of birth cannot be in the future.")
        return _kyc_redirect(request, "missing-dob")
    if dob.year < today.year - 120:
        messages.error(request, f"{client.name}: check the year — that is over 120 years ago.")
        return _kyc_redirect(request, "missing-dob")

    client.date_of_birth = dob
    client.save(update_fields=["date_of_birth"])
    note = ""
    if (client.age or 0) >= client_alerts.TRIGGER_AGE:
        # Typing a DOB is how a 45-year-old first becomes visible as one; the
        # daily cron only ever sees today's birthdays, so say where the alert
        # comes from rather than leaving it silently unraised.
        note = (f" They are {client.age} — run "
                f"`retirement_alerts --backlog --apply` to raise their "
                f"retirement-planning task.")
    messages.success(request, f"Date of birth saved for {client.name} (age {client.age}).{note}")
    return _kyc_redirect(request, "missing-dob")


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
        return _kyc_redirect(request)

    duplicate = Client.objects.filter(pan__iexact=pan).exclude(id=client.id).first()
    if duplicate:
        messages.error(
            request,
            f"PAN {pan} already belongs to '{duplicate.name}' (#{duplicate.id}) — "
            f"merge the two profiles instead of assigning the same PAN twice.",
        )
        return _kyc_redirect(request)

    client.pan = pan
    client.save(update_fields=["pan"])
    messages.success(request, f"PAN saved for {client.name}.")
    return _kyc_redirect(request)


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

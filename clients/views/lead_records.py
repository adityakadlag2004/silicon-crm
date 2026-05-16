"""Lead Records — lightweight spreadsheet system for tracking incoming leads.

Each LeadSheet is a tab with user-defined columns. Records (rows) store values
in a JSONB field keyed by column slug, so adding/removing columns doesn't
require migrations.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ..models import (
    Client,
    Employee,
    LeadSheet,
    LeadSheetColumn,
    LeadSheetFollowUp,
    LeadSheetRecord,
    Notification,
    Product,
    Sale,
)


def _notify_assignment(record: "LeadSheetRecord", actor):
    """Best-effort: ping the assignee that they got a lead."""
    if not record.assigned_to_id or not record.assigned_to.user_id:
        return
    if record.assigned_to.user_id == getattr(actor, "id", None):
        return  # don't notify yourself
    try:
        sheet_name = record.sheet.name
        # Pull a friendly identifier from the row's values
        vals = record.values or {}
        ident = (vals.get("name") or vals.get("full_name")
                 or vals.get("phone") or f"row #{record.id}")
        Notification.objects.create(
            recipient=record.assigned_to.user,
            title=f"New lead assigned: {ident}",
            body=f"You've been assigned a row in '{sheet_name}'.",
            link=f"/clients/leads/sheets/{record.sheet_id}/records/{record.id}/",
        )
    except Exception:
        pass


# ── Helpers ──────────────────────────────────────────────────────────────────

def _user_emp(request):
    return getattr(request.user, "employee", None)


def _is_admin(request) -> bool:
    emp = _user_emp(request)
    return request.user.is_superuser or (emp is not None and emp.role == "admin")


def _accessible_sheets(request):
    """Sheets the current user is allowed to see."""
    if _is_admin(request):
        return LeadSheet.objects.all()
    emp = _user_emp(request)
    if not emp:
        return LeadSheet.objects.none()
    # Owner OR (not private AND (no shared list OR they're in it))
    return LeadSheet.objects.filter(
        Q(owner=emp)
        | (Q(is_private=False) & (Q(shared_with=emp) | Q(shared_with__isnull=True)))
    ).distinct()


def _full_visibility(request, sheet) -> bool:
    """True if the user may see ALL rows in the sheet (manage view).
    Plain employees who are merely shared on the sheet only see their
    own assigned rows."""
    if request.user.is_superuser:
        return True
    emp = _user_emp(request)
    if emp is None:
        return False
    if emp.role in ("admin", "manager"):
        return True
    return sheet.owner_id == emp.id


def _visible_records(request, sheet):
    """Base record queryset scoped to what this user may see."""
    qs = sheet.records.all()
    if _full_visibility(request, sheet):
        return qs
    emp = _user_emp(request)
    if emp is None:
        return qs.none()
    return qs.filter(assigned_to=emp)


def _can_touch_record(request, sheet, record) -> bool:
    """Whether the user may view/edit a specific record."""
    if _full_visibility(request, sheet):
        return True
    emp = _user_emp(request)
    return bool(emp and record.assigned_to_id == emp.id)


def _assignment_pool(sheet: LeadSheet):
    """Employees who should receive new rows in round-robin order.

    Only the explicitly chosen sheet members participate in distribution —
    the owner and admins still see everything but don't get rows assigned
    unless they're also in shared_with.

    - Firm-wide (not private, no shared_with) → no auto-assign (returns []).
    - Private sheet → no auto-assign (owner sees all rows directly).
    - Shared with employees → pool = shared_with members ONLY.
    """
    if sheet.is_private:
        return []
    return list(sheet.shared_with.all())


def _round_robin(sheet: LeadSheet, n: int):
    """Yield N assignees, picking the employee with the fewest current
    assignments at each step (so re-importing tops up the lighter loads)."""
    pool = _assignment_pool(sheet)
    if not pool:
        for _ in range(n):
            yield None
        return
    counts = {emp.id: 0 for emp in pool}
    rows = (
        LeadSheetRecord.objects.filter(sheet=sheet, assigned_to__in=pool)
        .values("assigned_to_id")
        .annotate(c=Count("id"))
    )
    for r in rows:
        counts[r["assigned_to_id"]] = r["c"]
    for _ in range(n):
        pick = min(pool, key=lambda e: (counts[e.id], e.id))
        yield pick
        counts[pick.id] += 1


def _unique_field_key(sheet: LeadSheet, base: str) -> str:
    """Derive a unique slug for a column on this sheet."""
    base = slugify(base) or "col"
    base = base.replace("-", "_")
    candidate = base
    n = 2
    existing = set(sheet.columns.values_list("field_key", flat=True))
    while candidate in existing:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def _sanitize_value(col: LeadSheetColumn, raw):
    """Coerce a raw user input into the right shape for the column type."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if col.type == LeadSheetColumn.TYPE_NUMBER:
        if not s:
            return ""
        try:
            # Keep as string but verify it parses, so the JSONB value is stable.
            float(s)
            return s
        except ValueError:
            return ""
    if col.type == LeadSheetColumn.TYPE_DATE:
        if not s:
            return ""
        # Accept YYYY-MM-DD or DD/MM/YYYY
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        return ""
    if col.type == LeadSheetColumn.TYPE_PHONE:
        # Strip spaces and dashes
        return re.sub(r"[\s\-]", "", s)[:20]
    if col.type in (LeadSheetColumn.TYPE_SELECT, LeadSheetColumn.TYPE_STATUS):
        opts = list(col.options or [])
        if s in opts:
            return s
        # Auto-add new option (so users can extend dropdowns by typing)
        if s and len(opts) < 50:
            opts.append(s)
            col.options = opts
            col.save(update_fields=["options"])
        return s
    return s[:1000]  # text/email cap


# ── List + create ────────────────────────────────────────────────────────────

@login_required
def lead_sheets_list(request):
    qs = _accessible_sheets(request).select_related("product", "owner__user")
    if not _is_admin(request):
        qs = qs.filter(archived=False)
    show_archived = request.GET.get("archived") == "1"
    if not show_archived:
        qs = qs.filter(archived=False)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))

    sheets = list(qs)

    # Annotate row counts in a single grouped query
    counts = dict(
        LeadSheetRecord.objects.filter(sheet__in=sheets)
        .values("sheet_id")
        .annotate(c=Count("id"))
        .values_list("sheet_id", "c")
    )
    for s in sheets:
        s.record_count = counts.get(s.id, 0)

    products = Product.objects.filter(is_active=True).order_by("display_order", "name")
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username")

    return render(request, "leads/sheets_list.html", {
        "sheets": sheets,
        "q": q,
        "show_archived": show_archived,
        "is_admin": _is_admin(request),
        "products": products,
        "employees": employees,
    })


@login_required
@require_POST
def lead_sheet_create(request):
    emp = _user_emp(request)
    if emp is None and not request.user.is_superuser:
        return HttpResponseForbidden("Need an employee account to create sheets.")

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Sheet name is required.")
        return redirect("clients:lead_sheets")

    description = (request.POST.get("description") or "").strip()
    is_private = request.POST.get("is_private") == "on"
    product_id = request.POST.get("product") or None
    shared_ids = request.POST.getlist("shared_with")

    sheet = LeadSheet.objects.create(
        name=name,
        description=description,
        is_private=is_private,
        owner=emp,
        product_id=int(product_id) if product_id else None,
    )
    if not is_private and shared_ids:
        sheet.shared_with.set(Employee.objects.filter(id__in=shared_ids))

    # Seed with sensible default columns so the sheet isn't empty
    defaults = [
        ("Name", "name", LeadSheetColumn.TYPE_TEXT),
        ("Phone", "phone", LeadSheetColumn.TYPE_PHONE),
        ("Email", "email", LeadSheetColumn.TYPE_EMAIL),
        ("Source", "source", LeadSheetColumn.TYPE_TEXT),
        ("Status", "status", LeadSheetColumn.TYPE_STATUS),
        ("Notes", "notes", LeadSheetColumn.TYPE_TEXT),
    ]
    for i, (col_name, key, col_type) in enumerate(defaults):
        opts = ["new", "contacted", "interested", "converted", "dropped"] if col_type == LeadSheetColumn.TYPE_STATUS else []
        LeadSheetColumn.objects.create(
            sheet=sheet, name=col_name, field_key=key, type=col_type,
            options=opts, display_order=i,
        )

    messages.success(request, f"Sheet '{sheet.name}' created.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


# ── Detail / table view ──────────────────────────────────────────────────────

def _apply_filters_sort(qs, request, columns):
    """Apply filter + sort query params to a record queryset.
    Returns (filtered_qs, applied_filter_summary)."""
    summary = {}

    # Tag filter (any of)
    tag_filters = [t for t in request.GET.getlist("tag") if t.strip()]
    if tag_filters:
        # JSONField contains: at least one matching tag
        from django.db.models import Q as _Q
        tag_q = _Q()
        for t in tag_filters:
            tag_q |= _Q(tags__contains=[t])
        qs = qs.filter(tag_q)
        summary["tags"] = tag_filters

    # Status filter (any column of type=status that has the value)
    status_filter = (request.GET.get("status") or "").strip()
    if status_filter:
        status_cols = [c for c in columns if c.type == "status"]
        if status_cols:
            from django.db.models import Q as _Q
            q = _Q()
            for c in status_cols:
                q |= _Q(values__contains={c.field_key: status_filter})
            qs = qs.filter(q)
            summary["status"] = status_filter

    # Assignee filter
    assignee_filter = (request.GET.get("assignee") or "").strip()
    if assignee_filter:
        try:
            qs = qs.filter(assigned_to_id=int(assignee_filter))
            summary["assignee"] = int(assignee_filter)
        except ValueError:
            pass

    # Sorting. sort=col_<field_key> or -col_<field_key> for value columns;
    # sort=created / -created / assigned / -assigned for fixed columns.
    sort = (request.GET.get("sort") or "-created").strip()
    if sort.lstrip("-") == "created":
        qs = qs.order_by(("-" if sort.startswith("-") else "") + "created_at", "-id")
    elif sort.lstrip("-") == "assigned":
        qs = qs.order_by(("-" if sort.startswith("-") else "") + "assigned_to__user__username", "-id")
    elif sort.startswith("col_") or sort.startswith("-col_"):
        desc = sort.startswith("-")
        key = sort.lstrip("-")[len("col_"):]
        # Use raw JSONB key path for ordering
        from django.db.models.expressions import RawSQL
        qs = qs.annotate(_sort_v=RawSQL("(values ->> %s)", (key,)))
        qs = qs.order_by(("-" if desc else "") + "_sort_v", "-id")
    else:
        qs = qs.order_by("-created_at", "-id")
    summary["sort"] = sort
    return qs, summary


@login_required
def lead_sheet_detail(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_view(request.user):
        return HttpResponseForbidden("You don't have access to this sheet.")

    columns = list(sheet.columns.all())
    full_view = _full_visibility(request, sheet)
    base_qs = _visible_records(request, sheet).select_related("converted_client", "assigned_to__user")

    # "Mine" / "Unassigned" filters — only meaningful for full-visibility users;
    # plain employees are already restricted to their own rows.
    scope = (request.GET.get("scope") or "").strip()
    user_emp = _user_emp(request)
    if full_view:
        if scope == "mine" and user_emp:
            base_qs = base_qs.filter(assigned_to=user_emp)
        elif scope == "unassigned":
            base_qs = base_qs.filter(assigned_to__isnull=True)
    else:
        scope = ""  # ignore scope param for restricted users

    base_qs, applied_filters = _apply_filters_sort(base_qs, request, columns)
    records = list(base_qs)
    # Pre-compute each record's per-column value so the template can iterate
    # cleanly without needing a custom dict-lookup filter.
    for r in records:
        vals = r.values or {}
        r.cells = [(col, vals.get(col.field_key, "")) for col in columns]
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username")

    has_phone_col = any(c.type == LeadSheetColumn.TYPE_PHONE for c in columns)
    has_email_col = any(c.type == LeadSheetColumn.TYPE_EMAIL for c in columns)

    # Distinct set of tags currently in this sheet (for autosuggest datalist)
    # Use a fresh queryset (unfiltered) so suggestions stay stable across filters.
    all_tags_rows = sheet.records.values_list("tags", flat=True)
    available_tags = sorted({t for tag_list in all_tags_rows for t in (tag_list or [])})

    # Per-employee record counts (owner/admin/manager only — the
    # distribution overview is a management tool, not for plain employees).
    if full_view and not sheet.is_private:
        from collections import Counter
        rows = sheet.records.values_list("assigned_to_id", flat=True)
        cnt = Counter(rows)
        share_counts = []
        for emp in _assignment_pool(sheet):
            share_counts.append({"emp": emp, "count": cnt.get(emp.id, 0)})
        unassigned_count = cnt.get(None, 0)
    else:
        share_counts = []
        unassigned_count = 0

    # Status options across the sheet (for the status filter dropdown)
    status_options = []
    for col in columns:
        if col.type == "status":
            status_options.extend(col.options or [])
    status_options = sorted(set(status_options))

    sort_param = (request.GET.get("sort") or "-created").strip()

    return render(request, "leads/sheet_detail.html", {
        "sheet": sheet,
        "columns": columns,
        "records": records,
        "can_edit": sheet.can_edit(request.user),
        "is_owner": sheet.owner_id == (user_emp.id if user_emp else None),
        "is_admin": _is_admin(request),
        "employees": employees,
        "column_types": LeadSheetColumn.TYPE_CHOICES,
        "has_phone_col": has_phone_col,
        "has_email_col": has_email_col,
        "available_tags": available_tags,
        "scope": scope,
        "full_view": full_view,
        "share_counts": share_counts,
        "unassigned_count": unassigned_count,
        "status_options": status_options,
        "sort_param": sort_param,
        "applied_filters": applied_filters,
        "active_filter_tags": request.GET.getlist("tag"),
        "active_filter_status": request.GET.get("status", ""),
        "active_filter_assignee": request.GET.get("assignee", ""),
    })


# ── Access management (modal POST) ───────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_access(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    emp = _user_emp(request)
    if not (request.user.is_superuser or (emp and (sheet.owner_id == emp.id or emp.role == "admin"))):
        return HttpResponseForbidden("Only the sheet owner or an admin can change access.")

    sheet.is_private = request.POST.get("is_private") == "on"
    shared_ids = request.POST.getlist("shared_with")
    if sheet.is_private:
        sheet.shared_with.clear()
    else:
        sheet.shared_with.set(Employee.objects.filter(id__in=shared_ids))
    sheet.save(update_fields=["is_private", "updated_at"])
    messages.success(request, "Access updated.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


# ── Column management ────────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_column_add(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Column name required.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    col_type = request.POST.get("type") or LeadSheetColumn.TYPE_TEXT
    if col_type not in dict(LeadSheetColumn.TYPE_CHOICES):
        col_type = LeadSheetColumn.TYPE_TEXT
    options_raw = (request.POST.get("options") or "").strip()
    options = [o.strip() for o in options_raw.split(",") if o.strip()] if options_raw else []
    next_order = (sheet.columns.count() or 0)

    LeadSheetColumn.objects.create(
        sheet=sheet,
        name=name,
        field_key=_unique_field_key(sheet, name),
        type=col_type,
        options=options,
        display_order=next_order,
    )
    messages.success(request, f"Column '{name}' added.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


@login_required
@require_POST
def lead_sheet_column_delete(request, sheet_id, column_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    col = get_object_or_404(LeadSheetColumn, id=column_id, sheet=sheet)
    col.delete()
    messages.success(request, f"Column '{col.name}' removed (existing record values are preserved as orphan keys).")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


# ── Record CRUD ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_record_add(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")

    columns = list(sheet.columns.all())
    values = {}
    for col in columns:
        raw = request.POST.get(f"col_{col.field_key}", "")
        values[col.field_key] = _sanitize_value(col, raw)

    assignee = next(_round_robin(sheet, 1), None)
    record = LeadSheetRecord.objects.create(
        sheet=sheet, values=values,
        created_by=request.user, updated_by=request.user,
        assigned_to=assignee,
    )
    sheet.save(update_fields=["updated_at"])  # bump timestamp
    if assignee:
        _notify_assignment(record, request.user)
    msg = "Row added"
    if assignee:
        msg += f" — assigned to {assignee.user.username}"
    messages.success(request, msg + ".")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


@login_required
@require_POST
def lead_sheet_record_update(request, sheet_id, record_id):
    """AJAX-friendly cell update: POST body has field_key=<col slug> and value=<new value>."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return JsonResponse({"ok": False, "error": "not your row"}, status=403)
    field_key = (request.POST.get("field_key") or "").strip()
    if not field_key:
        return HttpResponseBadRequest("field_key required")
    try:
        col = sheet.columns.get(field_key=field_key)
    except LeadSheetColumn.DoesNotExist:
        return JsonResponse({"ok": False, "error": "unknown column"}, status=400)

    new_val = _sanitize_value(col, request.POST.get("value", ""))
    record.values = {**record.values, field_key: new_val}
    record.updated_by = request.user
    record.save(update_fields=["values", "updated_by", "updated_at"])
    sheet.save(update_fields=["updated_at"])
    return JsonResponse({"ok": True, "value": new_val})


@login_required
@require_POST
def lead_sheet_record_delete(request, sheet_id, record_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return HttpResponseForbidden("This row isn't assigned to you.")
    record.delete()
    sheet.save(update_fields=["updated_at"])
    messages.success(request, "Row deleted.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


# ── CSV export (current view, with filters applied) ──────────────────────────

@login_required
def lead_sheet_export_csv(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_view(request.user):
        return HttpResponseForbidden("No access.")

    columns = list(sheet.columns.all())
    qs = _visible_records(request, sheet).select_related("assigned_to__user")

    # Honor the same filters as the detail view (scope only for full-view users)
    if _full_visibility(request, sheet):
        scope = (request.GET.get("scope") or "").strip()
        user_emp = _user_emp(request)
        if scope == "mine" and user_emp:
            qs = qs.filter(assigned_to=user_emp)
        elif scope == "unassigned":
            qs = qs.filter(assigned_to__isnull=True)
    qs, _ = _apply_filters_sort(qs, request, columns)

    # Stream CSV (handles large sheets without buffering the whole thing)
    from django.http import StreamingHttpResponse

    class _Echo:
        def write(self, value):
            return value

    pseudo = csv.writer(_Echo())

    def rows():
        # Header
        header = [c.name for c in columns] + ["Tags", "Assigned To", "Status", "Converted Client ID", "Created At"]
        yield pseudo.writerow(header)
        for r in qs.iterator(chunk_size=200):
            vals = r.values or {}
            row = [vals.get(c.field_key, "") for c in columns]
            row.append(", ".join(r.tags or []))
            row.append(r.assigned_to.user.username if r.assigned_to else "")
            row.append("converted" if r.converted_client_id else "")
            row.append(r.converted_client_id or "")
            row.append(r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "")
            yield pseudo.writerow(row)

    filename = f"{sheet.name.lower().replace(' ', '_')}-{timezone.now().strftime('%Y%m%d')}.csv"
    response = StreamingHttpResponse(rows(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ── CSV bulk import ──────────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_import_csv(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")

    upload = request.FILES.get("csv_file")
    if not upload:
        messages.error(request, "Please choose a CSV file.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)
    if upload.size > 5 * 1024 * 1024:
        messages.error(request, "CSV file too large (max 5 MB).")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    dedupe_phone = request.POST.get("dedupe_phone") == "on"
    dedupe_email = request.POST.get("dedupe_email") == "on"

    try:
        text = upload.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        headers = [h.strip() for h in (reader.fieldnames or [])]
        if not headers:
            messages.error(request, "CSV has no headers.")
            return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

        # Map CSV headers → existing columns by name (case-insensitive),
        # creating new text columns for any unknown headers.
        existing_by_name = {c.name.lower(): c for c in sheet.columns.all()}
        header_to_col = {}
        next_order = sheet.columns.count()
        for h in headers:
            col = existing_by_name.get(h.lower())
            if col is None:
                col = LeadSheetColumn.objects.create(
                    sheet=sheet, name=h, field_key=_unique_field_key(sheet, h),
                    type=LeadSheetColumn.TYPE_TEXT, display_order=next_order,
                )
                next_order += 1
            header_to_col[h] = col

        # Identify which columns to dedupe on (any column of that type)
        phone_cols = [c for c in sheet.columns.all() if c.type == LeadSheetColumn.TYPE_PHONE] if dedupe_phone else []
        email_cols = [c for c in sheet.columns.all() if c.type == LeadSheetColumn.TYPE_EMAIL] if dedupe_email else []

        # Pre-load existing values to compare against
        def _norm_phone(s):
            return re.sub(r"[\s\-]", "", str(s or "")).lower()
        def _norm_email(s):
            return str(s or "").strip().lower()

        existing_phones = set()
        existing_emails = set()
        if phone_cols or email_cols:
            for r in sheet.records.only("values").iterator():
                vals = r.values or {}
                for c in phone_cols:
                    p = _norm_phone(vals.get(c.field_key, ""))
                    if p:
                        existing_phones.add(p)
                for c in email_cols:
                    e = _norm_email(vals.get(c.field_key, ""))
                    if e:
                        existing_emails.add(e)

        rows_to_create = []
        skipped_blank = 0
        skipped_dupe = 0
        seen_phones_in_csv = set()
        seen_emails_in_csv = set()

        for row in reader:
            values = {}
            for h, col in header_to_col.items():
                values[col.field_key] = _sanitize_value(col, row.get(h, ""))
            if not any(v for v in values.values()):
                skipped_blank += 1
                continue

            # Check duplicates BOTH against existing rows AND within this CSV
            is_dupe = False
            if phone_cols:
                for c in phone_cols:
                    p = _norm_phone(values.get(c.field_key, ""))
                    if p and (p in existing_phones or p in seen_phones_in_csv):
                        is_dupe = True
                        break
                    if p:
                        seen_phones_in_csv.add(p)
            if not is_dupe and email_cols:
                for c in email_cols:
                    e = _norm_email(values.get(c.field_key, ""))
                    if e and (e in existing_emails or e in seen_emails_in_csv):
                        is_dupe = True
                        break
                    if e:
                        seen_emails_in_csv.add(e)

            if is_dupe:
                skipped_dupe += 1
                continue

            rows_to_create.append(LeadSheetRecord(
                sheet=sheet, values=values,
                created_by=request.user, updated_by=request.user,
            ))

        # Round-robin auto-assign across the share pool BEFORE saving.
        assignees = list(_round_robin(sheet, len(rows_to_create)))
        for row, who in zip(rows_to_create, assignees):
            row.assigned_to = who

        with transaction.atomic():
            previous_count = sheet.records.count()
            created = LeadSheetRecord.objects.bulk_create(rows_to_create)
            sheet.save(update_fields=["updated_at"])
        # Notify each new row's assignee (best-effort, deduped per user via batching)
        for r in created:
            _notify_assignment(r, request.user)

        added = len(rows_to_create)
        new_total = previous_count + added
        parts = [
            f"Added {added} row{'s' if added != 1 else ''}",
            f"({previous_count} → {new_total} total)",
        ]
        if skipped_dupe:
            parts.append(f"· skipped {skipped_dupe} duplicate{'s' if skipped_dupe != 1 else ''}")
        if skipped_blank:
            parts.append(f"· skipped {skipped_blank} blank")
        # Summarize assignment
        from collections import Counter
        assigned_counts = Counter(a.user.username for a in assignees if a is not None)
        if assigned_counts:
            dist = ", ".join(f"{u}:{c}" for u, c in sorted(assigned_counts.items()))
            parts.append(f"· distributed → {dist}")
        messages.success(request, " ".join(parts) + ".")
    except Exception as e:
        messages.error(request, f"Import failed: {e}")

    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


# ── Convert row → Client ─────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_record_convert(request, sheet_id, record_id):
    """Create a Client from this record. Maps known field_keys (name/phone/email/pan)
    to Client fields. Stores the resulting client_id back on the record."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return HttpResponseForbidden("This row isn't assigned to you.")

    if record.converted_client_id:
        messages.info(request, "This row is already linked to a client.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    v = record.values or {}
    name = (v.get("name") or v.get("full_name") or "").strip()
    if not name:
        messages.error(request, "Need a 'name' (or 'full_name') value to create a client.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    client = Client.objects.create(
        name=name,
        phone=(v.get("phone") or v.get("mobile") or "")[:15] or None,
        email=(v.get("email") or "") or None,
        pan=(v.get("pan") or "")[:20] or None,
        address=v.get("address") or None,
    )
    record.converted_client = client
    record.updated_by = request.user
    record.save(update_fields=["converted_client", "updated_by", "updated_at"])
    messages.success(request, f"Created client #{client.id} from this row.")
    return redirect("clients:client_profile", client_id=client.id)


# ── Tags (per-row, AJAX) ─────────────────────────────────────────────────────

_TAG_MAX = 32
_TAGS_PER_RECORD_MAX = 12


def _normalize_tag(s: str) -> str:
    """Lowercase, trim, replace whitespace with dash, drop punctuation, cap length."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-_]", "", s)  # only alphanumeric, dash, underscore survive
    s = re.sub(r"-+", "-", s).strip("-_")  # collapse runs and trim leading/trailing
    return s[:_TAG_MAX]


@login_required
@require_POST
def lead_sheet_record_tag_add(request, sheet_id, record_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return JsonResponse({"ok": False, "error": "not your row"}, status=403)
    tag = _normalize_tag(request.POST.get("tag", ""))
    if not tag:
        return JsonResponse({"ok": False, "error": "empty tag"}, status=400)
    tags = list(record.tags or [])
    if tag in tags:
        return JsonResponse({"ok": True, "tags": tags, "noop": True})
    if len(tags) >= _TAGS_PER_RECORD_MAX:
        return JsonResponse({"ok": False, "error": f"max {_TAGS_PER_RECORD_MAX} tags per row"}, status=400)
    tags.append(tag)
    record.tags = tags
    record.updated_by = request.user
    record.save(update_fields=["tags", "updated_by", "updated_at"])
    sheet.save(update_fields=["updated_at"])
    return JsonResponse({"ok": True, "tags": tags, "added": tag})


@login_required
@require_POST
def lead_sheet_record_tag_remove(request, sheet_id, record_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return JsonResponse({"ok": False, "error": "not your row"}, status=403)
    tag = _normalize_tag(request.POST.get("tag", ""))
    tags = [t for t in (record.tags or []) if t != tag]
    record.tags = tags
    record.updated_by = request.user
    record.save(update_fields=["tags", "updated_by", "updated_at"])
    sheet.save(update_fields=["updated_at"])
    return JsonResponse({"ok": True, "tags": tags, "removed": tag})


# ── Record profile / detail ──────────────────────────────────────────────────

@login_required
def lead_sheet_record_detail(request, sheet_id, record_id):
    """Profile page for a single lead-sheet row, showing all values + the
    full follow-up timeline."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_view(request.user):
        return HttpResponseForbidden("You don't have access to this sheet.")
    record = get_object_or_404(
        LeadSheetRecord.objects.select_related("converted_client", "created_by", "updated_by"),
        id=record_id, sheet=sheet,
    )
    if not _can_touch_record(request, sheet, record):
        return HttpResponseForbidden("This row isn't assigned to you.")
    columns = list(sheet.columns.all())
    vals = record.values or {}
    cells = [(col, vals.get(col.field_key, "")) for col in columns]
    followups = list(
        record.followups.select_related("created_by", "completed_by").order_by("completed", "scheduled_at")
    )
    pending_followups = [f for f in followups if not f.completed]
    completed_followups = [f for f in followups if f.completed]

    return render(request, "leads/record_detail.html", {
        "sheet": sheet,
        "record": record,
        "cells": cells,
        "pending_followups": pending_followups,
        "completed_followups": completed_followups,
        "can_edit": sheet.can_edit(request.user),
        "now": timezone.now(),
    })


# ── Follow-up actions ────────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_followup_add(request, sheet_id, record_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return HttpResponseForbidden("This row isn't assigned to you.")

    scheduled_raw = (request.POST.get("scheduled_at") or "").strip()
    note = (request.POST.get("note") or "").strip()
    if not scheduled_raw:
        messages.error(request, "Pick a date/time for the follow-up.")
        return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)

    # Accept "YYYY-MM-DDTHH:MM" (HTML datetime-local) and a few other shapes.
    parsed = None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(scheduled_raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        messages.error(request, "Could not parse the follow-up date/time.")
        return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)

    LeadSheetFollowUp.objects.create(
        record=record, scheduled_at=parsed, note=note, created_by=request.user,
    )
    sheet.save(update_fields=["updated_at"])
    messages.success(request, "Follow-up scheduled.")
    # Stay on whichever page they came from
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)


@login_required
@require_POST
def lead_sheet_followup_done(request, sheet_id, record_id, followup_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    fu = get_object_or_404(LeadSheetFollowUp, id=followup_id, record_id=record_id, record__sheet=sheet)
    if not _can_touch_record(request, sheet, fu.record):
        return HttpResponseForbidden("This row isn't assigned to you.")
    fu.completed = True
    fu.completed_at = timezone.now()
    fu.completed_by = request.user
    fu.completion_note = (request.POST.get("completion_note") or "").strip()
    fu.save()
    messages.success(request, "Marked complete.")
    return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record_id)


@login_required
@require_POST
def lead_sheet_followup_delete(request, sheet_id, record_id, followup_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    fu = get_object_or_404(LeadSheetFollowUp, id=followup_id, record_id=record_id, record__sheet=sheet)
    if not _can_touch_record(request, sheet, fu.record):
        return HttpResponseForbidden("This row isn't assigned to you.")
    fu.delete()
    messages.success(request, "Follow-up removed.")
    return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record_id)


# ── Bulk actions ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_bulk(request, sheet_id):
    """Apply a bulk action to selected records.

    POST params:
      action       — 'tag-add' | 'tag-remove' | 'assign' | 'unassign' | 'delete'
      record_ids[] — list of record IDs to act on
      tag          — for tag-* actions
      employee_id  — for assign action
    """
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")

    action = (request.POST.get("action") or "").strip()
    ids = request.POST.getlist("record_ids")
    if not ids:
        messages.warning(request, "No rows selected.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    # Scope to rows the user may touch (employees: only their assigned rows)
    # Assign / unassign are management actions — owner/admin/manager only.
    if action in ("assign", "unassign") and not _full_visibility(request, sheet):
        return HttpResponseForbidden("Only the sheet owner / admin / manager can (un)assign rows.")

    qs = _visible_records(request, sheet).filter(id__in=ids)
    n = qs.count()
    if n == 0:
        messages.warning(request, "No matching rows found.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    if action == "delete":
        qs.delete()
        messages.success(request, f"Deleted {n} row{'s' if n != 1 else ''}.")
    elif action == "tag-add":
        tag = _normalize_tag(request.POST.get("tag", ""))
        if not tag:
            messages.error(request, "Tag is empty after normalization.")
            return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)
        with transaction.atomic():
            for r in qs:
                tags = list(r.tags or [])
                if tag not in tags and len(tags) < 12:
                    tags.append(tag)
                    r.tags = tags
                    r.updated_by = request.user
                    r.save(update_fields=["tags", "updated_by", "updated_at"])
        messages.success(request, f"Added tag '{tag}' to {n} row{'s' if n != 1 else ''}.")
    elif action == "tag-remove":
        tag = _normalize_tag(request.POST.get("tag", ""))
        with transaction.atomic():
            for r in qs:
                tags = [t for t in (r.tags or []) if t != tag]
                if tags != (r.tags or []):
                    r.tags = tags
                    r.updated_by = request.user
                    r.save(update_fields=["tags", "updated_by", "updated_at"])
        messages.success(request, f"Removed tag '{tag}' from {n} row{'s' if n != 1 else ''}.")
    elif action == "assign":
        emp_id = request.POST.get("employee_id", "").strip()
        if not emp_id:
            messages.error(request, "Pick an employee.")
            return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)
        try:
            emp = Employee.objects.get(pk=int(emp_id))
        except (Employee.DoesNotExist, ValueError):
            messages.error(request, "Invalid employee.")
            return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)
        affected = list(qs)  # snapshot before update so we can notify
        qs.update(assigned_to=emp, updated_by=request.user, updated_at=timezone.now())
        for r in affected:
            r.assigned_to = emp
            _notify_assignment(r, request.user)
        messages.success(request, f"Assigned {n} row{'s' if n != 1 else ''} to {emp.user.username}.")
    elif action == "unassign":
        qs.update(assigned_to=None, updated_by=request.user, updated_at=timezone.now())
        messages.success(request, f"Unassigned {n} row{'s' if n != 1 else ''}.")
    else:
        messages.error(request, f"Unknown bulk action: {action}")

    sheet.save(update_fields=["updated_at"])
    return redirect(request.META.get("HTTP_REFERER") or reverse("clients:lead_sheet_detail", args=[sheet.id]))


# ── Manual assign + redistribute ─────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_record_assign(request, sheet_id, record_id):
    """Manually set the assignee of one row. POST employee_id, or empty to unassign."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    if not _full_visibility(request, sheet):
        return HttpResponseForbidden("Only the sheet owner / admin / manager can reassign rows.")
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)

    raw = (request.POST.get("employee_id") or "").strip()
    if not raw:
        record.assigned_to = None
    else:
        try:
            emp = Employee.objects.get(pk=int(raw))
        except (Employee.DoesNotExist, ValueError):
            messages.error(request, "Invalid employee.")
            return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)
        record.assigned_to = emp
    record.updated_by = request.user
    record.save(update_fields=["assigned_to", "updated_by", "updated_at"])
    sheet.save(update_fields=["updated_at"])
    if record.assigned_to_id:
        _notify_assignment(record, request.user)
    name = record.assigned_to.user.username if record.assigned_to else "—"
    messages.success(request, f"Row #{record.id} assigned to {name}.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


@login_required
@require_POST
def lead_sheet_distribute(request, sheet_id):
    """Round-robin all UNASSIGNED rows across the share pool. Owner/admin only."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    user_emp = _user_emp(request)
    if not (request.user.is_superuser or (user_emp and (sheet.owner_id == user_emp.id or user_emp.role == "admin"))):
        return HttpResponseForbidden("Only the sheet owner or an admin can redistribute.")

    pool = _assignment_pool(sheet)
    if not pool:
        messages.warning(request, "No share pool to distribute to. Share the sheet with employees first.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    unassigned = list(sheet.records.filter(assigned_to__isnull=True).order_by("id"))
    if not unassigned:
        messages.info(request, "No unassigned rows to distribute.")
        return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)

    assignees = list(_round_robin(sheet, len(unassigned)))
    with transaction.atomic():
        for r, who in zip(unassigned, assignees):
            r.assigned_to = who
            r.updated_by = request.user
        LeadSheetRecord.objects.bulk_update(unassigned, ["assigned_to", "updated_by", "updated_at"])
        sheet.save(update_fields=["updated_at"])
    # Notify each assignee (best-effort)
    for r in unassigned:
        _notify_assignment(r, request.user)

    from collections import Counter
    cnt = Counter(a.user.username for a in assignees if a)
    summary = ", ".join(f"{u}:{c}" for u, c in sorted(cnt.items()))
    messages.success(request, f"Distributed {len(unassigned)} unassigned row{'s' if len(unassigned) != 1 else ''} → {summary}")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


# ── Sheet analytics ──────────────────────────────────────────────────────────

@login_required
def lead_sheet_stats(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_view(request.user):
        return HttpResponseForbidden("No access.")

    records = list(_visible_records(request, sheet).select_related("assigned_to__user"))
    total = len(records)
    converted = sum(1 for r in records if r.converted_client_id)
    conv_rate = round((converted / total) * 100, 1) if total else 0

    # Status distribution (across all status columns)
    status_cols = [c for c in sheet.columns.all() if c.type == "status"]
    from collections import Counter
    status_counter = Counter()
    for r in records:
        v = r.values or {}
        for c in status_cols:
            val = v.get(c.field_key)
            if val:
                status_counter[val] += 1
    status_dist = sorted(status_counter.items(), key=lambda kv: -kv[1])

    # Per-assignee counts
    assignee_counter = Counter()
    for r in records:
        assignee_counter[r.assigned_to.user.username if r.assigned_to else "Unassigned"] += 1
    assignee_dist = sorted(assignee_counter.items(), key=lambda kv: -kv[1])

    # Tag distribution
    tag_counter = Counter()
    for r in records:
        for t in (r.tags or []):
            tag_counter[t] += 1
    tag_dist = sorted(tag_counter.items(), key=lambda kv: -kv[1])[:15]

    # Records added per day (last 30 days)
    today = timezone.now().date()
    day_counter = Counter()
    for r in records:
        if r.created_at:
            day_counter[r.created_at.date()] += 1
    timeline = []
    for i in range(29, -1, -1):
        d = today - __import__("datetime").timedelta(days=i)
        timeline.append({"label": d.strftime("%d %b"), "count": day_counter.get(d, 0)})

    import json as _json
    return render(request, "leads/sheet_stats.html", {
        "sheet": sheet,
        "total": total,
        "converted": converted,
        "conv_rate": conv_rate,
        "status_dist": status_dist,
        "assignee_dist": assignee_dist,
        "tag_dist": tag_dist,
        "timeline_labels_json": _json.dumps([t["label"] for t in timeline]),
        "timeline_counts_json": _json.dumps([t["count"] for t in timeline]),
    })


# ── Convert row → Sale ───────────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_record_to_sale(request, sheet_id, record_id):
    """Create a pending Sale from this row. Reuses the linked Client if the
    row was already converted, otherwise creates one from the row values."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return HttpResponseForbidden("No edit permission.")
    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
    if not _can_touch_record(request, sheet, record):
        return HttpResponseForbidden("This row isn't assigned to you.")

    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(str(request.POST.get("amount") or "0"))
    except (InvalidOperation, ValueError):
        amount = Decimal("0")
    if amount <= 0:
        messages.error(request, "Enter a valid sale amount.")
        return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)

    product_name = (request.POST.get("product") or "").strip()
    if not product_name and sheet.product_id:
        product_name = sheet.product.name
    if not product_name:
        messages.error(request, "Pick a product for the sale.")
        return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)

    # Resolve / create the Client
    client = record.converted_client
    if client is None:
        v = record.values or {}
        name = (v.get("name") or v.get("full_name") or "").strip()
        if not name:
            messages.error(request, "Row needs a 'name' value to create the client+sale.")
            return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)
        client = Client.objects.create(
            name=name,
            phone=(v.get("phone") or v.get("mobile") or "")[:15] or None,
            email=(v.get("email") or "") or None,
            pan=(v.get("pan") or "")[:20] or None,
            address=v.get("address") or None,
        )
        record.converted_client = client

    # Employee = the row's assignee, else the acting user's employee
    emp = record.assigned_to or _user_emp(request)
    if emp is None:
        messages.error(request, "No employee to attribute the sale to (assign the row first).")
        return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)

    product_ref = Product.objects.filter(name__iexact=product_name).first()
    sale = Sale.objects.create(
        client=client,
        employee=emp,
        product=product_name,
        product_ref=product_ref,
        amount=amount,
        status=Sale.STATUS_PENDING,
    )
    record.updated_by = request.user
    record.save(update_fields=["converted_client", "updated_by", "updated_at"])
    messages.success(
        request,
        f"Created pending sale #{sale.id} (₹{amount:,.0f}) for {client.name}. "
        f"It will appear in Approve Sales for review."
    )
    return redirect("clients:lead_sheet_record_detail", sheet_id=sheet.id, record_id=record.id)


# ── Global search across all accessible sheets ───────────────────────────────

@login_required
def lead_records_search(request):
    """Search every record (across all sheets the user can see) by value or tag."""
    q = (request.GET.get("q") or "").strip()
    results = []
    if q:
        sheets = _accessible_sheets(request).filter(archived=False)
        sheet_ids = list(sheets.values_list("id", flat=True))
        # icontains over the JSONB text + tags. Cast values to text for a
        # cheap substring match; good enough for an operator-facing search.
        from django.db.models.expressions import RawSQL
        qs = (
            LeadSheetRecord.objects.filter(sheet_id__in=sheet_ids)
            .annotate(_vtext=RawSQL("values::text", []))
            .filter(Q(_vtext__icontains=q) | Q(tags__icontains=q))
            .select_related("sheet", "assigned_to__user")
            .order_by("-created_at")
        )
        # Restrict to rows the user may actually see: full-visibility on a
        # sheet → all its rows; otherwise only rows assigned to them.
        emp = _user_emp(request)
        full_sheet_ids = {
            s.id for s in sheets if _full_visibility(request, s)
        }
        if not (request.user.is_superuser or (emp and emp.role in ("admin", "manager"))):
            qs = qs.filter(Q(sheet_id__in=full_sheet_ids) | Q(assigned_to=emp))
        qs = qs[:100]
        for r in qs:
            vals = r.values or {}
            preview = " · ".join(
                f"{k}: {v}" for k, v in list(vals.items())[:4] if v
            )
            results.append({"record": r, "preview": preview})

    return render(request, "leads/records_search.html", {
        "q": q,
        "results": results,
    })


# ── Archive / unarchive sheet ────────────────────────────────────────────────

@login_required
@require_POST
def lead_sheet_archive(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    emp = _user_emp(request)
    if not (request.user.is_superuser or (emp and (sheet.owner_id == emp.id or emp.role == "admin"))):
        return HttpResponseForbidden("Only the sheet owner or an admin can archive.")
    sheet.archived = not sheet.archived
    sheet.save(update_fields=["archived", "updated_at"])
    messages.success(request, "Sheet archived." if sheet.archived else "Sheet unarchived.")
    return redirect("clients:lead_sheets")

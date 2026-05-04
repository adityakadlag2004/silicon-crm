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
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ..models import (
    Client,
    Employee,
    LeadSheet,
    LeadSheetColumn,
    LeadSheetRecord,
    Product,
)


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

@login_required
def lead_sheet_detail(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_view(request.user):
        return HttpResponseForbidden("You don't have access to this sheet.")

    columns = list(sheet.columns.all())
    records = list(sheet.records.select_related("converted_client").order_by("-created_at", "-id"))
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__username")

    return render(request, "leads/sheet_detail.html", {
        "sheet": sheet,
        "columns": columns,
        "records": records,
        "can_edit": sheet.can_edit(request.user),
        "is_owner": sheet.owner_id == (_user_emp(request).id if _user_emp(request) else None),
        "is_admin": _is_admin(request),
        "employees": employees,
        "column_types": LeadSheetColumn.TYPE_CHOICES,
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

    LeadSheetRecord.objects.create(
        sheet=sheet, values=values, created_by=request.user, updated_by=request.user,
    )
    sheet.save(update_fields=["updated_at"])  # bump timestamp
    messages.success(request, "Row added.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


@login_required
@require_POST
def lead_sheet_record_update(request, sheet_id, record_id):
    """AJAX-friendly cell update: POST body has field_key=<col slug> and value=<new value>."""
    sheet = get_object_or_404(LeadSheet, id=sheet_id)
    if not sheet.can_edit(request.user):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    record = get_object_or_404(LeadSheetRecord, id=record_id, sheet=sheet)
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
    record.delete()
    sheet.save(update_fields=["updated_at"])
    messages.success(request, "Row deleted.")
    return redirect("clients:lead_sheet_detail", sheet_id=sheet.id)


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

        rows_to_create = []
        skipped = 0
        for row in reader:
            values = {}
            for h, col in header_to_col.items():
                values[col.field_key] = _sanitize_value(col, row.get(h, ""))
            if not any(v for v in values.values()):
                skipped += 1
                continue
            rows_to_create.append(LeadSheetRecord(
                sheet=sheet, values=values,
                created_by=request.user, updated_by=request.user,
            ))

        with transaction.atomic():
            LeadSheetRecord.objects.bulk_create(rows_to_create)
            sheet.save(update_fields=["updated_at"])

        msg = f"Imported {len(rows_to_create)} row{'s' if len(rows_to_create) != 1 else ''}"
        if skipped:
            msg += f" (skipped {skipped} blank row{'s' if skipped != 1 else ''})"
        messages.success(request, msg + ".")
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

"""JSON API for the native Android screens (mobile/NATIVE_MIGRATION.md).

Same auth model as views/calls.py: the native app sends the WebView's
session cookie + X-CSRFToken header. One endpoint set is added here per
converted screen.
"""
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import (
    CallFollowUp,
    Client,
    Employee,
    Notification,
    Product,
    Renewal,
    Sale,
)
from .helpers import get_manager_access


def _emp(request):
    return getattr(request.user, "employee", None)


def _is_admin(request):
    emp = _emp(request)
    return request.user.is_superuser or (emp is not None and emp.role == "admin")


def _money(value):
    """Decimal → float for JSON (display-only figures)."""
    return float(value or 0)


@login_required
@require_GET
def app_dashboard(request):
    """Everything the native dashboard screen needs, in one call."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    today = timezone.localdate()

    # Admins see the whole firm; everyone else sees their own numbers.
    sales_scope = Sale.objects.all() if is_admin else Sale.objects.filter(employee=emp)
    approved = sales_scope.filter(status=Sale.STATUS_APPROVED)

    today_agg = approved.filter(date=today).aggregate(n=Count("id"), amount=Sum("amount"))
    month_agg = approved.filter(date__year=today.year, date__month=today.month).aggregate(
        n=Count("id"), amount=Sum("amount"), points=Sum("points")
    )

    data = {
        "role": "admin" if is_admin else (emp.role if emp else "unknown"),
        "name": request.user.get_full_name() or request.user.username,
        "today": {
            "sales_count": today_agg["n"] or 0,
            "amount": _money(today_agg["amount"]),
        },
        "month": {
            "sales_count": month_agg["n"] or 0,
            "amount": _money(month_agg["amount"]),
            "points": _money(month_agg["points"]),
        },
        "unread_notifications": Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count(),
        "pending_followups": (
            CallFollowUp.objects.filter(
                employee=emp, status=CallFollowUp.STATUS_PENDING
            ).count()
            if emp else 0
        ),
        "recent_sales": [
            {
                "id": s.id,
                "client": s.client.name if s.client_id else "",
                "employee": s.employee.user.username if s.employee_id else "",
                "product": s.product or "",
                "amount": _money(s.amount),
                "status": s.status,
                "date": s.date.isoformat() if s.date else "",
            }
            for s in sales_scope.select_related("client", "employee__user")
            .order_by("-created_at")[:8]
        ],
    }

    if is_admin:
        data["pending_approvals"] = Sale.objects.filter(status=Sale.STATUS_PENDING).count()
        data["unmapped_clients"] = Client.objects.filter(mapped_to__isnull=True).count()

    return JsonResponse(data)


# ── Screen 2: Add Sale ───────────────────────────────────────────────────────

def _product_flags(p):
    name = (p.name or "").strip().lower()
    is_health = p.code == "HEALTH_INS" or name == "health insurance"
    is_insurance = is_health or p.code == "LIFE_INS" or name == "life insurance"
    return is_health, is_insurance


@login_required
@require_GET
def app_sale_meta(request):
    """Products + (for admins) employee list for the Add Sale screen."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    products = []
    for p in Product.objects.filter(
        is_active=True, archived_at__isnull=True,
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).order_by("display_order", "name"):
        is_health, is_insurance = _product_flags(p)
        products.append({
            "id": p.id, "name": p.name,
            "is_health": is_health, "is_insurance": is_insurance,
        })
    data = {
        "is_admin": is_admin,
        "employee_id": emp.id if emp else None,
        "products": products,
    }
    if is_admin:
        data["employees"] = [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ]
    return JsonResponse(data)


@login_required
@require_POST
def app_sale_create(request):
    """Create a sale. Mirrors the web add_sale rules: only admins may
    attribute to another employee; admin sales auto-approve."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    client = Client.objects.filter(pk=body.get("client_id")).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Select a client."}, status=400)

    product = Product.objects.filter(
        pk=body.get("product_id"), is_active=True, archived_at__isnull=True,
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).first()
    if product is None:
        return JsonResponse({"ok": False, "error": "Select a product."}, status=400)

    try:
        amount = Decimal(str(body.get("amount")))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError):
        return JsonResponse({"ok": False, "error": "Enter a valid amount."}, status=400)

    cover_amount = None
    raw_cover = body.get("cover_amount")
    if raw_cover not in (None, ""):
        try:
            cover_amount = Decimal(str(raw_cover))
        except InvalidOperation:
            return JsonResponse({"ok": False, "error": "Invalid cover amount."}, status=400)

    policy_type = body.get("policy_type") or ""
    if policy_type not in ("", Sale.POLICY_TYPE_FRESH, Sale.POLICY_TYPE_PORT):
        return JsonResponse({"ok": False, "error": "Invalid policy type."}, status=400)

    sale_emp = emp
    if is_admin and body.get("employee_id"):
        sale_emp = Employee.objects.filter(pk=body.get("employee_id"), active=True).first() or emp
    if sale_emp is None:
        return JsonResponse({"ok": False, "error": "Your account is not mapped to an employee."}, status=403)

    sale = Sale(
        client=client, employee=sale_emp, product=product.name, product_ref=product,
        amount=amount, cover_amount=cover_amount, policy_type=policy_type,
    )
    if is_admin:
        sale.status = Sale.STATUS_APPROVED
        sale.approved_by = request.user
        sale.approved_at = timezone.now()
    sale._audit_actor = request.user
    sale.save()
    return JsonResponse({"ok": True, "id": sale.id, "status": sale.status})


# ── Screen 3: Clients ────────────────────────────────────────────────────────

_PAGE = 25


@login_required
@require_GET
def app_clients(request):
    emp = _emp(request)
    scope = request.GET.get("scope", "my")
    q = (request.GET.get("q") or "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1

    qs = Client.objects.select_related("mapped_to__user").order_by("name")
    if scope == "my" and emp:
        qs = qs.filter(mapped_to=emp)
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(phone__icontains=q)
            | Q(email__icontains=q) | Q(pan__icontains=q)
        )

    start, end = (page - 1) * _PAGE, page * _PAGE
    rows = list(qs[start:end + 1])  # +1 to detect has_more
    has_more = len(rows) > _PAGE
    return JsonResponse({
        "results": [
            {
                "id": c.id,
                "name": c.name,
                "phone": c.phone or "",
                "email": c.email or "",
                "mapped_to": (
                    c.mapped_to.user.get_full_name() or c.mapped_to.user.username
                ) if c.mapped_to and c.mapped_to.user_id else "",
            }
            for c in rows[:_PAGE]
        ],
        "has_more": has_more,
        "page": page,
    })


@login_required
@require_GET
def app_client_detail(request, client_id):
    c = get_object_or_404(Client.objects.select_related("mapped_to__user"), pk=client_id)
    sales = list(
        Sale.objects.filter(client=c).select_related("employee__user")
        .order_by("-date", "-id")[:10]
    )
    renewal_agg = Renewal.objects.filter(client=c).aggregate(
        n=Count("id"), premium=Sum("premium_amount")
    )
    return JsonResponse({
        "id": c.id,
        "name": c.name,
        "phone": c.phone or "",
        "email": c.email or "",
        "pan": c.pan or "",
        "address": c.address or "",
        "mapped_to": (
            c.mapped_to.user.get_full_name() or c.mapped_to.user.username
        ) if c.mapped_to and c.mapped_to.user_id else "",
        "sip_status": c.sip_status,
        "health_status": c.health_status,
        "life_status": c.life_status,
        "renewals": {"count": renewal_agg["n"] or 0, "premium": _money(renewal_agg["premium"])},
        "recent_sales": [
            {
                "id": s.id, "product": s.product or "", "amount": _money(s.amount),
                "status": s.status, "date": s.date.isoformat() if s.date else "",
            }
            for s in sales
        ],
    })


# ── Screen 4: Call follow-ups ────────────────────────────────────────────────

def _fu_row(fu, now):
    return {
        "id": fu.id,
        "phone": fu.phone,
        "client": fu.client.name if fu.client_id else "",
        "client_id": fu.client_id,
        "scheduled_at": timezone.localtime(fu.scheduled_at).strftime("%d %b, %I:%M %p"),
        "overdue": fu.scheduled_at <= now,
        "note": fu.note or "",
        "status": fu.status,
    }


_SERIOUS_CALL_SECONDS = 150  # calls longer than this count as "serious"


def _today_call_stats(emp):
    """The employee's own call performance for today, within the admin office-
    hours window (so it matches what Call Analytics shows for them)."""
    from ..models import CallLogEntry, CallTrackingSettings

    cfg = CallTrackingSettings.current()
    today = timezone.localdate()
    qs = CallLogEntry.objects.filter(
        employee=emp,
        started_at__date=today,
        started_at__time__gte=cfg.work_start,
        started_at__time__lt=cfg.work_end,
    )
    agg = qs.aggregate(
        calls=Count("id"),
        connected_count=Count("id", filter=Q(connected=True)),
        talk_seconds=Sum("duration_seconds", filter=Q(connected=True)),
        serious=Count("id", filter=Q(duration_seconds__gt=_SERIOUS_CALL_SECONDS)),
    )
    return {
        "calls": agg["calls"] or 0,
        "connected": agg["connected_count"] or 0,
        "talk_minutes": round((agg["talk_seconds"] or 0) / 60, 1),
        "serious": agg["serious"] or 0,
    }


@login_required
@require_GET
def app_followups(request):
    emp = _emp(request)
    if emp is None:
        return JsonResponse({"pending": [], "stats": None})
    now = timezone.now()
    # Only pending — completed/dismissed follow-ups drop off the screen.
    pending = CallFollowUp.objects.filter(
        employee=emp, status=CallFollowUp.STATUS_PENDING
    ).select_related("client").order_by("scheduled_at")[:100]
    return JsonResponse({
        "pending": [_fu_row(f, now) for f in pending],
        "stats": _today_call_stats(emp),
    })


@login_required
@require_POST
def app_followup_action(request, followup_id):
    emp = _emp(request)
    fu = get_object_or_404(CallFollowUp, pk=followup_id)
    if not (_is_admin(request) or (emp and fu.employee_id == emp.id)):
        return JsonResponse({"ok": False, "error": "Not your follow-up."}, status=403)
    try:
        action = json.loads(request.body.decode("utf-8")).get("action")
    except Exception:
        action = None

    if action == "done":
        fu.status = CallFollowUp.STATUS_DONE
        fu.completed_at = timezone.now()
        fu.save(update_fields=["status", "completed_at"])
    elif action == "dismiss":
        fu.status = CallFollowUp.STATUS_DISMISSED
        fu.completed_at = timezone.now()
        fu.save(update_fields=["status", "completed_at"])
    elif action == "snooze":
        fu.scheduled_at = timezone.now() + timedelta(hours=1)
        fu.reminded = False
        fu.save(update_fields=["scheduled_at", "reminded"])
    else:
        return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
    return JsonResponse({"ok": True})


# ── Screen 5: Sales list + approvals ─────────────────────────────────────────

@login_required
@require_GET
def app_sales(request):
    emp = _emp(request)
    is_admin = _is_admin(request)
    is_manager = bool(emp and emp.role == "manager")
    access = get_manager_access() if is_manager else None
    can_approve = is_admin or bool(access and access.allow_approve_sales)

    qs = Sale.objects.select_related("client", "employee__user").order_by("-date", "-created_at")
    if not is_admin and not (is_manager and access and access.allow_view_all_sales):
        qs = qs.filter(employee=emp) if emp else qs.none()

    status = request.GET.get("status", "")
    if status in (Sale.STATUS_PENDING, Sale.STATUS_APPROVED, Sale.STATUS_REJECTED):
        qs = qs.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(client__name__icontains=q) | Q(product__icontains=q)
            | Q(employee__user__username__icontains=q)
        )
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    start, end = (page - 1) * _PAGE, page * _PAGE
    rows = list(qs[start:end + 1])
    return JsonResponse({
        "can_approve": can_approve,
        "has_more": len(rows) > _PAGE,
        "page": page,
        "results": [
            {
                "id": s.id,
                "client": s.client.name if s.client_id else "",
                "employee": (
                    s.employee.user.get_full_name() or s.employee.user.username
                ) if s.employee_id and s.employee.user_id else "",
                "product": s.product or "",
                "amount": _money(s.amount),
                "points": _money(s.points),
                "status": s.status,
                "date": s.date.strftime("%d %b %Y") if s.date else "",
            }
            for s in rows[:_PAGE]
        ],
    })


@login_required
@require_POST
def app_sale_action(request, sale_id):
    """Approve or reject a sale — same permissions as the web approve page."""
    from .sales import _recompute_sibling_sales

    emp = _emp(request)
    is_admin = _is_admin(request)
    is_manager = bool(emp and emp.role == "manager")
    access = get_manager_access() if is_manager else None
    if not (is_admin or (access and access.allow_approve_sales)):
        return JsonResponse({"ok": False, "error": "No permission."}, status=403)

    sale = get_object_or_404(Sale, pk=sale_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = {}
    action = body.get("action")

    if action == "approve":
        sale.status = Sale.STATUS_APPROVED
        sale.rejection_reason = ""
    elif action == "reject":
        sale.status = Sale.STATUS_REJECTED
        sale.rejection_reason = (body.get("reason") or "").strip()
    else:
        return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)

    sale.approved_by = request.user
    sale.approved_at = timezone.now()
    sale._audit_actor = request.user
    sale.save()
    _recompute_sibling_sales(sale)
    return JsonResponse({"ok": True, "status": sale.status})


@login_required
@require_POST
def app_logout(request):
    from django.contrib.auth import logout as django_logout
    django_logout(request)
    return JsonResponse({"ok": True})


# ── Screen 7: Renewals ───────────────────────────────────────────────────────

def _renewal_product_type(product):
    """Derive Renewal.product_type from a Product row."""
    name = (product.name or "").strip().lower()
    if product.code == "HEALTH_INS" or name == "health insurance":
        return Renewal.PRODUCT_TYPE_HEALTH, None
    if product.code == "LIFE_INS" or name == "life insurance":
        return Renewal.PRODUCT_TYPE_LIFE, None
    return Renewal.PRODUCT_TYPE_OTHER, product.name


@login_required
@require_GET
def app_renewal_meta(request):
    emp = _emp(request)
    is_admin = _is_admin(request)
    products = [
        {"id": p.id, "name": p.name}
        for p in Product.objects.filter(
            is_active=True, archived_at__isnull=True,
            domain__in=[Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH],
        ).order_by("display_order", "name")
    ]
    data = {
        "is_admin": is_admin,
        "products": products,
        "frequencies": [
            {"value": v, "label": l} for v, l in Renewal.FREQUENCY_CHOICES
        ],
    }
    if is_admin:
        data["employees"] = [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ]
    return JsonResponse(data)


@login_required
@require_GET
def app_renewals(request):
    emp = _emp(request)
    is_admin = _is_admin(request)
    is_manager = bool(emp and emp.role == "manager")

    qs = Renewal.objects.select_related("client", "employee__user", "product_ref")
    if not (is_admin or is_manager):
        qs = qs.filter(employee=emp) if emp else qs.none()

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(client__name__icontains=q) | Q(client__phone__icontains=q)
            | Q(product_name__icontains=q) | Q(product_ref__name__icontains=q)
        )

    today = timezone.localdate()
    month_qs = qs.filter(
        premium_collected_on__year=today.year, premium_collected_on__month=today.month
    )
    summary = {
        "month_premium": _money(month_qs.aggregate(t=Sum("premium_amount"))["t"]),
        "month_count": month_qs.count(),
        "today_premium": _money(
            qs.filter(premium_collected_on=today).aggregate(t=Sum("premium_amount"))["t"]
        ),
    }

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    start, end = (page - 1) * _PAGE, page * _PAGE
    rows = list(qs.order_by("-premium_collected_on", "-id")[start:end + 1])

    return JsonResponse({
        "summary": summary,
        "has_more": len(rows) > _PAGE,
        "page": page,
        "results": [
            {
                "id": r.id,
                "client": r.client.name if r.client_id else "",
                "product": r.product_ref.name if r.product_ref_id else (
                    r.product_name or r.get_product_type_display()
                ),
                "premium": _money(r.premium_amount),
                "frequency": r.get_frequency_display(),
                "renewal_date": r.renewal_date.strftime("%d %b %Y") if r.renewal_date else "",
                "collected_on": r.premium_collected_on.strftime("%d %b %Y") if r.premium_collected_on else "",
                "employee": (
                    r.employee.user.get_full_name() or r.employee.user.username
                ) if r.employee_id and r.employee.user_id else "",
            }
            for r in rows[:_PAGE]
        ],
    })


@login_required
@require_POST
def app_renewal_create(request):
    emp = _emp(request)
    is_admin = _is_admin(request)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    client = Client.objects.filter(pk=body.get("client_id")).first()
    if client is None:
        return JsonResponse({"ok": False, "error": "Select a client."}, status=400)

    product = Product.objects.filter(
        pk=body.get("product_id"),
        domain__in=[Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH],
    ).first()
    if product is None:
        return JsonResponse({"ok": False, "error": "Select a product."}, status=400)
    product_type, product_name = _renewal_product_type(product)

    try:
        premium = Decimal(str(body.get("premium_amount")))
        if premium <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError):
        return JsonResponse({"ok": False, "error": "Enter a valid premium amount."}, status=400)

    try:
        renewal_date = date.fromisoformat(str(body.get("renewal_date")))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Renewal date must be YYYY-MM-DD."}, status=400)

    frequency = body.get("frequency")
    if frequency not in dict(Renewal.FREQUENCY_CHOICES):
        return JsonResponse({"ok": False, "error": "Pick a frequency."}, status=400)

    renewal_emp = emp
    if is_admin and body.get("employee_id"):
        renewal_emp = Employee.objects.filter(pk=body.get("employee_id"), active=True).first() or emp

    renewal = Renewal.objects.create(
        client=client,
        product_ref=product,
        product_type=product_type,
        product_name=product_name,
        renewal_date=renewal_date,
        frequency=frequency,
        premium_amount=premium,
        employee=renewal_emp,
        notes=str(body.get("notes") or "").strip() or None,
        created_by=request.user,
    )
    return JsonResponse({"ok": True, "id": renewal.id})


# ── Screen 8: Notifications ──────────────────────────────────────────────────

@login_required
@require_GET
def app_notifications(request):
    notes = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:40]
    return JsonResponse({
        "unread": Notification.objects.filter(recipient=request.user, is_read=False).count(),
        "results": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "link": n.link or "",
                "is_read": n.is_read,
                "created_at": timezone.localtime(n.created_at).strftime("%d %b, %I:%M %p"),
            }
            for n in notes
        ],
    })


@login_required
@require_POST
def app_notifications_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return JsonResponse({"ok": True})


# ── Screen 9: Leads pipeline ─────────────────────────────────────────────────

from ..models import Lead, LeadProductProgress, LeadRemark  # noqa: E402
from django.db import transaction  # noqa: E402


def _lead_qs(request):
    """Same scoping as the web pipeline: employees see only their own leads."""
    emp = _emp(request)
    qs = Lead.objects.select_related("assigned_to__user").prefetch_related("progress_entries")
    if emp and emp.role == "employee":
        qs = qs.filter(assigned_to=emp)
    return qs


def _can_assign_leads(request):
    emp = _emp(request)
    return request.user.is_superuser or (emp and emp.role in ("admin", "manager"))


@login_required
@require_GET
def app_lead_meta(request):
    data = {
        "can_assign": _can_assign_leads(request),
        "products": [
            {"value": v, "label": l} for v, l in LeadProductProgress.PRODUCT_CHOICES
        ],
        "statuses": [
            {"value": v, "label": l} for v, l in LeadProductProgress.STATUS_CHOICES
        ],
    }
    if _can_assign_leads(request):
        data["employees"] = [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ]
    return JsonResponse(data)


def _progress_summary(lead):
    marks = {"pending": "·", "half_sold": "½", "processed": "✓"}
    out = []
    entries = {p.product: p.status for p in lead.progress_entries.all()}
    for key, label in (("health", "H"), ("life", "L"), ("wealth", "W")):
        out.append(f"{label}{marks.get(entries.get(key, 'pending'), '·')}")
    return " ".join(out)


@login_required
@require_GET
def app_leads(request):
    qs = _lead_qs(request)

    counts = {
        "pending": qs.filter(is_discarded=False, stage=Lead.STAGE_PENDING).count(),
        "half_sold": qs.filter(is_discarded=False, stage=Lead.STAGE_HALF).count(),
        "processed": qs.filter(is_discarded=False, stage=Lead.STAGE_PROCESSED).count(),
        "discarded": qs.filter(is_discarded=True).count(),
    }

    stage = request.GET.get("stage", "")
    if stage == "discarded":
        qs = qs.filter(is_discarded=True)
    else:
        qs = qs.filter(is_discarded=False)
        if stage in (Lead.STAGE_PENDING, Lead.STAGE_HALF, Lead.STAGE_PROCESSED):
            qs = qs.filter(stage=stage)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(customer_name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q)
        )

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    start, end = (page - 1) * _PAGE, page * _PAGE
    rows = list(qs.order_by("-updated_at")[start:end + 1])

    return JsonResponse({
        "counts": counts,
        "has_more": len(rows) > _PAGE,
        "page": page,
        "results": [
            {
                "id": l.id,
                "name": l.customer_name,
                "phone": l.phone or "",
                "stage": l.stage,
                "is_discarded": l.is_discarded,
                "converted": bool(l.converted_client_id),
                "progress": _progress_summary(l),
                "assigned_to": (
                    l.assigned_to.user.get_full_name() or l.assigned_to.user.username
                ) if l.assigned_to_id and l.assigned_to.user_id else "",
            }
            for l in rows[:_PAGE]
        ],
    })


@login_required
@require_GET
def app_lead_detail(request, lead_id):
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    remarks = lead.remarks.select_related("created_by").order_by("-created_at")[:15]
    progress = {p.product: p for p in lead.progress_entries.all()}
    return JsonResponse({
        "id": lead.id,
        "name": lead.customer_name,
        "phone": lead.phone or "",
        "email": lead.email or "",
        "income": _money(lead.income) if lead.income is not None else None,
        "expenses": _money(lead.expenses) if lead.expenses is not None else None,
        "notes": lead.notes or "",
        "stage": lead.stage,
        "is_discarded": lead.is_discarded,
        "converted_client_id": lead.converted_client_id,
        "can_convert": lead.stage == Lead.STAGE_PROCESSED and not lead.converted_client_id,
        "assigned_to": (
            lead.assigned_to.user.get_full_name() or lead.assigned_to.user.username
        ) if lead.assigned_to_id and lead.assigned_to.user_id else "",
        "progress": [
            {
                "product": key,
                "label": label,
                "status": progress[key].status if key in progress else "pending",
                "target": _money(progress[key].target_amount) if key in progress and progress[key].target_amount is not None else None,
                "achieved": _money(progress[key].achieved_amount) if key in progress and progress[key].achieved_amount is not None else None,
            }
            for key, label in LeadProductProgress.PRODUCT_CHOICES
        ],
        "remarks": [
            {
                "text": r.text,
                "by": r.created_by.username if r.created_by_id else "",
                "at": timezone.localtime(r.created_at).strftime("%d %b, %I:%M %p"),
            }
            for r in remarks
        ],
    })


@login_required
@require_POST
def app_lead_create(request):
    emp = _emp(request)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    name = str(body.get("customer_name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Customer name is required."}, status=400)

    assigned = emp
    if _can_assign_leads(request) and body.get("assigned_to_id"):
        assigned = Employee.objects.filter(pk=body.get("assigned_to_id"), active=True).first() or emp
    if assigned is None:
        return JsonResponse({"ok": False, "error": "No employee to assign the lead to."}, status=400)

    def _opt_decimal(key):
        raw = body.get(key)
        if raw in (None, ""):
            return None
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None

    lead = Lead.objects.create(
        customer_name=name[:255],
        phone=str(body.get("phone") or "").strip()[:20],
        email=str(body.get("email") or "").strip()[:254],
        income=_opt_decimal("income"),
        expenses=_opt_decimal("expenses"),
        notes=str(body.get("notes") or "").strip(),
        assigned_to=assigned,
        created_by=request.user,
    )
    # Seed the three product tracks like the web form's default rows.
    for product, _label in LeadProductProgress.PRODUCT_CHOICES:
        LeadProductProgress.objects.create(lead=lead, product=product)
    return JsonResponse({"ok": True, "id": lead.id})


@login_required
@require_POST
def app_lead_progress(request, lead_id):
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    product = body.get("product")
    if product not in dict(LeadProductProgress.PRODUCT_CHOICES):
        return JsonResponse({"ok": False, "error": "Unknown product."}, status=400)
    status = body.get("status")
    if status not in dict(LeadProductProgress.STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Unknown status."}, status=400)

    defaults = {"status": status}
    for field in ("target_amount", "achieved_amount"):
        raw = body.get(field)
        if raw not in (None, ""):
            try:
                defaults[field] = Decimal(str(raw))
            except InvalidOperation:
                return JsonResponse({"ok": False, "error": f"Invalid {field}."}, status=400)

    LeadProductProgress.objects.update_or_create(
        lead=lead, product=product, defaults=defaults
    )
    lead.refresh_from_db()
    return JsonResponse({"ok": True, "stage": lead.stage})


@login_required
@require_POST
def app_lead_remark(request, lead_id):
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    try:
        text = json.loads(request.body.decode("utf-8")).get("text", "").strip()
    except Exception:
        text = ""
    if not text:
        return JsonResponse({"ok": False, "error": "Remark text required."}, status=400)
    LeadRemark.objects.create(lead=lead, text=text[:2000], created_by=request.user)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def app_lead_action(request, lead_id):
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    try:
        action = json.loads(request.body.decode("utf-8")).get("action")
    except Exception:
        action = None

    if action == "discard":
        lead.is_discarded = True
        lead.save(update_fields=["is_discarded", "updated_at"])
    elif action == "undiscard":
        lead.is_discarded = False
        lead.save(update_fields=["is_discarded", "updated_at"])
    elif action == "convert":
        # Mirrors web lead_convert_to_client exactly.
        if lead.converted_client_id:
            return JsonResponse({"ok": False, "error": "Already converted."}, status=400)
        if lead.stage != Lead.STAGE_PROCESSED:
            return JsonResponse({"ok": False, "error": "Only processed leads can be converted."}, status=400)
        progress_map = {p.product: p for p in lead.progress_entries.all()}
        with transaction.atomic():
            client = Client(
                name=lead.customer_name,
                phone=lead.phone or None,
                email=lead.email or None,
                mapped_to=lead.assigned_to,
                status="Mapped" if lead.assigned_to else "Unmapped",
            )
            hp = progress_map.get("health")
            lp = progress_map.get("life")
            wp = progress_map.get("wealth")
            if hp and hp.status == LeadProductProgress.STATUS_PROCESSED:
                client.health_status = True
                client.health_cover = hp.achieved_amount
            if lp and lp.status == LeadProductProgress.STATUS_PROCESSED:
                client.life_status = True
                client.life_cover = lp.achieved_amount
            if wp and wp.status == LeadProductProgress.STATUS_PROCESSED:
                client.sip_status = True
                client.sip_amount = wp.achieved_amount
            client.save()
            lead.converted_client = client
            lead.save(update_fields=["converted_client", "updated_at"])
        return JsonResponse({"ok": True, "client_id": client.id})
    else:
        return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
    return JsonResponse({"ok": True})


# ── Screen 10: Reports summary ───────────────────────────────────────────────

@login_required
@require_GET
def app_report_summary(request):
    """Monthly trend + product mix + (admins/managers) employee leaderboard."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    is_manager = bool(emp and emp.role == "manager")
    firm_wide = is_admin or (is_manager and get_manager_access().allow_employee_performance)

    base = Sale.objects.filter(status=Sale.STATUS_APPROVED)
    if not firm_wide:
        if emp is None:
            return JsonResponse({"ok": False, "error": "No employee account."}, status=403)
        base = base.filter(employee=emp)

    today = timezone.localdate()
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()

    trend = []
    for (yy, mm) in months:
        agg = base.filter(date__year=yy, date__month=mm).aggregate(
            amount=Sum("amount"), n=Count("id")
        )
        trend.append({
            "label": date(yy, mm, 1).strftime("%b"),
            "amount": _money(agg["amount"]),
            "count": agg["n"] or 0,
        })

    month_qs = base.filter(date__year=today.year, date__month=today.month)
    products = [
        {"name": r["product"] or "Other", "amount": _money(r["t"]), "count": r["n"]}
        for r in month_qs.values("product").annotate(t=Sum("amount"), n=Count("id")).order_by("-t")
    ]

    data = {"firm_wide": firm_wide, "trend": trend, "products": products}

    if firm_wide:
        data["leaderboard"] = [
            {
                "name": r["employee__user__first_name"] or r["employee__user__username"],
                "amount": _money(r["t"]),
                "points": _money(r["p"]),
            }
            for r in month_qs.values(
                "employee__user__username", "employee__user__first_name"
            ).annotate(t=Sum("amount"), p=Sum("points")).order_by("-t")[:15]
        ]
    return JsonResponse(data)


# ── Device permission reporting (admin visibility on Call Analytics) ─────────

from ..models import AppDeviceStatus  # noqa: E402


@login_required
@require_POST
def app_device_status(request):
    """The app reports its permission state on every launch."""
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False}, status=400)
    AppDeviceStatus.objects.update_or_create(
        user=request.user,
        defaults={
            "calls_granted": bool(body.get("calls_granted")),
            "overlay_granted": bool(body.get("overlay_granted")),
            "notifications_granted": bool(body.get("notifications_granted")),
            "app_version": str(body.get("app_version") or "")[:20],
        },
    )
    return JsonResponse({"ok": True})


# ── Self-hosted app updates ──────────────────────────────────────────────────
# The app checks /api/app/version/ at launch; if the server has a newer
# versionCode it offers a one-tap download+install of /app/latest.apk.
# Publish a release with mobile/release.sh (uploads APK + version.json
# to MEDIA_ROOT/app/ on the server). Both endpoints are deliberately
# public: the APK is signed and contains no secrets, and the updater
# must work even before login.

import os as _os  # noqa: E402

from django.conf import settings as _settings  # noqa: E402
from django.http import FileResponse, Http404  # noqa: E402
from django.views.decorators.http import require_GET as _require_GET  # noqa: E402


def _app_dist_dir():
    return _os.path.join(str(_settings.MEDIA_ROOT), "app")


@_require_GET
def app_version(request):
    path = _os.path.join(_app_dist_dir(), "version.json")
    if not _os.path.exists(path):
        return JsonResponse({"available": False})
    try:
        with open(path) as f:
            info = json.load(f)
    except Exception:
        return JsonResponse({"available": False})
    return JsonResponse({
        "available": True,
        "version_code": int(info.get("version_code", 0)),
        "version_name": str(info.get("version_name", "")),
        "notes": str(info.get("notes", "")),
        "url": "https://" + request.get_host() + "/clients/app/latest.apk",
    })


@_require_GET
def app_apk_download(request):
    path = _os.path.join(_app_dist_dir(), "latest.apk")
    if not _os.path.exists(path):
        raise Http404("No app build published.")
    resp = FileResponse(open(path, "rb"), content_type="application/vnd.android.package-archive")
    resp["Content-Disposition"] = 'attachment; filename="KadlagBO.apk"'
    return resp


# ── Native Call Analytics (admin-only) ───────────────────────────────────────

from ..models import CallLogEntry, CallTrackingSettings  # noqa: E402


@login_required
@require_GET
def app_call_analytics(request):
    """Employee-wise call drill-down for the native screen.
    Params: employee_id (optional), range = today|week|month."""
    if not _is_admin(request):
        return JsonResponse({"ok": False, "error": "Admins only."}, status=403)

    cfg = CallTrackingSettings.current()
    today = timezone.localdate()
    rng = request.GET.get("range", "today")
    if rng not in ("today", "week", "month"):
        rng = "today"

    qs = CallLogEntry.objects.filter(
        started_at__time__gte=cfg.work_start,
        started_at__time__lt=cfg.work_end,
    )
    if rng == "today":
        qs = qs.filter(started_at__date=today)
    elif rng == "week":
        qs = qs.filter(started_at__date__gte=today - timedelta(days=6))
    else:
        qs = qs.filter(started_at__year=today.year, started_at__month=today.month)

    try:
        emp_id = int(request.GET.get("employee_id", ""))
        qs = qs.filter(employee_id=emp_id)
    except (TypeError, ValueError):
        pass

    totals = qs.aggregate(
        dialed=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING)),
        connected_calls=Count("id", filter=Q(connected=True)),
        received=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_INCOMING, connected=True)),
        missed=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_INCOMING, connected=False)),
        talk_seconds=Sum("duration_seconds", filter=Q(connected=True)),
    )

    # Per-employee team breakdown for the same timeframe (ignores the
    # employee_id filter — always the whole team, so admins can see who's
    # doing what). Includes active employees with zero calls.
    breakdown_qs = CallLogEntry.objects.filter(
        started_at__time__gte=cfg.work_start,
        started_at__time__lt=cfg.work_end,
    )
    if rng == "today":
        breakdown_qs = breakdown_qs.filter(started_at__date=today)
    elif rng == "week":
        breakdown_qs = breakdown_qs.filter(started_at__date__gte=today - timedelta(days=6))
    else:
        breakdown_qs = breakdown_qs.filter(started_at__year=today.year, started_at__month=today.month)

    per_emp = {
        row["employee_id"]: row
        for row in breakdown_qs.values("employee_id").annotate(
            calls=Count("id"),
            connected_count=Count("id", filter=Q(connected=True)),
            talk_seconds=Sum("duration_seconds", filter=Q(connected=True)),
            serious=Count("id", filter=Q(duration_seconds__gt=_SERIOUS_CALL_SECONDS)),
        )
    }
    by_employee = []
    for e in Employee.objects.filter(active=True).select_related("user"):
        r = per_emp.get(e.id)
        by_employee.append({
            "id": e.id,
            "name": e.user.get_full_name() or e.user.username if e.user_id else f"#{e.id}",
            "calls": (r["calls"] if r else 0),
            "connected": (r["connected_count"] if r else 0),
            "talk_minutes": round(((r["talk_seconds"] if r else 0) or 0) / 60, 1),
            "serious": (r["serious"] if r else 0),
        })
    by_employee.sort(key=lambda x: (-x["calls"], -x["talk_minutes"]))

    return JsonResponse({
        "totals": {
            "dialed": totals["dialed"] or 0,
            "connected": totals["connected_calls"] or 0,
            "received": totals["received"] or 0,
            "missed": totals["missed"] or 0,
            "talk_minutes": round((totals["talk_seconds"] or 0) / 60, 1),
        },
        "by_employee": by_employee,
        "employees": [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ],
        "calls": [
            {
                "time": timezone.localtime(c.started_at).strftime("%d %b, %I:%M %p"),
                "employee": (
                    c.employee.user.get_full_name() or c.employee.user.username
                ) if c.employee_id and c.employee.user_id else "",
                "direction": c.direction,
                "phone": c.phone,
                "client": c.client.name if c.client_id else "",
                "duration": c.duration_seconds,
                "connected": c.connected,
            }
            for c in qs.select_related("employee__user", "client").order_by("-started_at")[:100]
        ],
    })


# ── Screen 11: Team management (admin only) ──────────────────────────────────

from itertools import cycle as _cycle  # noqa: E402

from django.contrib.auth.models import User as _User  # noqa: E402
from django.contrib.auth.password_validation import validate_password as _validate_password  # noqa: E402
from django.core.exceptions import ValidationError as _ValidationError  # noqa: E402
from django.db import transaction as _transaction  # noqa: E402

from ..models import AuditLog  # noqa: E402


def _team_forbidden(request):
    if not _is_admin(request):
        return JsonResponse({"ok": False, "error": "Admins only."}, status=403)
    return None


@login_required
@require_GET
def app_team(request):
    denied = _team_forbidden(request)
    if denied:
        return denied
    today = timezone.localdate()
    rows = []
    for e in Employee.objects.select_related("user").order_by("-active", "user__first_name", "user__username"):
        if not e.user_id:
            continue
        month = Sale.objects.filter(
            employee=e, status=Sale.STATUS_APPROVED,
            date__year=today.year, date__month=today.month,
        ).aggregate(amount=Sum("amount"), n=Count("id"))
        rows.append({
            "id": e.id,
            "name": e.user.get_full_name() or e.user.username,
            "username": e.user.username,
            "role": e.role,
            "active": e.active,
            "employee_number": e.employee_number or "",
            "client_count": Client.objects.filter(mapped_to=e).count(),
            "month_amount": _money(month["amount"]),
            "month_sales": month["n"] or 0,
        })
    return JsonResponse({
        "results": rows,
        "roles": [
            {"value": v, "label": l}
            for v, l in Employee._meta.get_field("role").choices
        ],
    })


@login_required
@require_GET
def app_team_detail(request, employee_id):
    denied = _team_forbidden(request)
    if denied:
        return denied
    e = get_object_or_404(Employee.objects.select_related("user"), pk=employee_id)
    today = timezone.localdate()
    approved = Sale.objects.filter(employee=e, status=Sale.STATUS_APPROVED)
    month = approved.filter(date__year=today.year, date__month=today.month)
    return JsonResponse({
        "id": e.id,
        "username": e.user.username if e.user_id else "",
        "first_name": e.user.first_name if e.user_id else "",
        "last_name": e.user.last_name if e.user_id else "",
        "email": e.user.email if e.user_id else "",
        "role": e.role,
        "active": e.active,
        "salary": _money(e.salary),
        "employee_number": e.employee_number or "",
        "stats": {
            "total_sales": Sale.objects.filter(employee=e).count(),
            "pending_sales": Sale.objects.filter(employee=e, status=Sale.STATUS_PENDING).count(),
            "total_amount": _money(approved.aggregate(t=Sum("amount"))["t"]),
            "total_points": _money(approved.aggregate(t=Sum("points"))["t"]),
            "clients": Client.objects.filter(mapped_to=e).count(),
            "month_amount": _money(month.aggregate(t=Sum("amount"))["t"]),
            "month_points": _money(month.aggregate(t=Sum("points"))["t"]),
        },
    })


def _team_validate(body, exclude_emp=None):
    """Shared validation for create/update. Returns (cleaned, error)."""
    valid_roles = dict(Employee._meta.get_field("role").choices)
    role = (body.get("role") or "").strip()
    if role not in valid_roles:
        return None, f"Invalid role '{role}'."
    raw_salary = str(body.get("salary") or "0").strip()
    try:
        salary = Decimal(raw_salary)
        if salary < 0:
            raise InvalidOperation
    except InvalidOperation:
        return None, "Salary must be a non-negative number."
    number = str(body.get("employee_number") or "").strip() or None
    if number:
        qs = Employee.objects.filter(employee_number=number)
        if exclude_emp is not None:
            qs = qs.exclude(pk=exclude_emp.pk)
        if qs.exists():
            return None, f"Employee number '{number}' is already in use."
    return {
        "role": role,
        "salary": salary,
        "employee_number": number,
        "first_name": str(body.get("first_name") or "").strip()[:150],
        "last_name": str(body.get("last_name") or "").strip()[:150],
        "email": str(body.get("email") or "").strip()[:254],
    }, None


@login_required
@require_POST
def app_team_create(request):
    denied = _team_forbidden(request)
    if denied:
        return denied
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    username = str(body.get("username") or "").strip()
    if not username:
        return JsonResponse({"ok": False, "error": "Username is required."}, status=400)
    if _User.objects.filter(username__iexact=username).exists():
        return JsonResponse({"ok": False, "error": "Username already exists."}, status=400)

    password = str(body.get("password") or "")
    try:
        _validate_password(password)
    except _ValidationError as e:
        return JsonResponse({"ok": False, "error": " ".join(e.messages)}, status=400)

    cleaned, err = _team_validate(body)
    if err:
        return JsonResponse({"ok": False, "error": err}, status=400)

    from .team import _next_employee_number
    with _transaction.atomic():
        user = _User.objects.create_user(
            username=username,
            password=password,
            email=cleaned["email"],
            first_name=cleaned["first_name"],
            last_name=cleaned["last_name"],
            is_active=True,
        )
        emp = Employee.objects.create(
            user=user,
            role=cleaned["role"],
            salary=cleaned["salary"],
            employee_number=cleaned["employee_number"] or _next_employee_number(),
            active=True,
        )
    return JsonResponse({"ok": True, "id": emp.id})


@login_required
@require_POST
def app_team_update(request, employee_id):
    denied = _team_forbidden(request)
    if denied:
        return denied
    e = get_object_or_404(Employee.objects.select_related("user"), pk=employee_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)
    cleaned, err = _team_validate(body, exclude_emp=e)
    if err:
        return JsonResponse({"ok": False, "error": err}, status=400)

    user = e.user
    user.first_name = cleaned["first_name"]
    user.last_name = cleaned["last_name"]
    user.email = cleaned["email"]
    user.save(update_fields=["first_name", "last_name", "email"])

    old_role = e.role
    e.role = cleaned["role"]
    e.salary = cleaned["salary"]
    e.employee_number = cleaned["employee_number"]
    e.save(update_fields=["role", "salary", "employee_number"])

    if old_role != e.role:
        AuditLog.objects.create(
            action=AuditLog.ACTION_EMPLOYEE_ROLE_CHANGED,
            actor=request.user,
            target_model="Employee",
            target_id=e.pk,
            summary=f"Role of '{user.username}' changed: {old_role} → {e.role}",
            details={"from": old_role, "to": e.role, "via": "app"},
        )
    return JsonResponse({"ok": True})


@login_required
@require_POST
def app_team_toggle(request, employee_id):
    """Activate/deactivate — mirrors web team_toggle_status incl. round-robin
    client reassignment on deactivation."""
    denied = _team_forbidden(request)
    if denied:
        return denied
    e = get_object_or_404(Employee.objects.select_related("user"), pk=employee_id)

    if e.active:
        others = list(Employee.objects.filter(active=True).exclude(pk=e.pk))
        mapped = list(Client.objects.filter(mapped_to=e))
        if mapped and not others:
            return JsonResponse(
                {"ok": False, "error": "Cannot deactivate: last active employee with mapped clients."},
                status=400,
            )
        with _transaction.atomic():
            if others:
                rr = _cycle(others)
                for c in mapped:
                    c.reassign_to(next(rr), changed_by=request.user, note="Auto-reassigned on deactivation (app)")
            e.active = False
            e.save(update_fields=["active"])
            if e.user_id:
                e.user.is_active = False
                e.user.save(update_fields=["is_active"])
        return JsonResponse({"ok": True, "active": False, "reassigned": len(mapped)})

    with _transaction.atomic():
        e.active = True
        e.save(update_fields=["active"])
        if e.user_id:
            e.user.is_active = True
            e.user.save(update_fields=["is_active"])
    return JsonResponse({"ok": True, "active": True, "reassigned": 0})


@login_required
@require_POST
def app_team_reset_password(request, employee_id):
    denied = _team_forbidden(request)
    if denied:
        return denied
    e = get_object_or_404(Employee.objects.select_related("user"), pk=employee_id)
    try:
        password = json.loads(request.body.decode("utf-8")).get("new_password") or ""
    except Exception:
        password = ""
    try:
        _validate_password(password, user=e.user)
    except _ValidationError as err:
        return JsonResponse({"ok": False, "error": " ".join(err.messages)}, status=400)
    e.user.set_password(password)
    e.user.save()
    return JsonResponse({"ok": True})


# ── Add Client (Clients screen "+ Add") ──────────────────────────────────────

@login_required
@require_POST
def app_client_create(request):
    """Create a client. Mirrors the web add form: name + phone required.
    Employees' clients map to themselves; admins may pick anyone or leave
    unmapped."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    name = str(body.get("name") or "").strip()
    phone = str(body.get("phone") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Client name is required."}, status=400)
    if not phone:
        return JsonResponse({"ok": False, "error": "Phone number is required."}, status=400)

    digits = "".join(ch for ch in phone if ch.isdigit())
    if Client.objects.filter(phone__endswith=digits[-10:]).exists() and len(digits) >= 10:
        return JsonResponse({"ok": False, "error": "A client with this phone number already exists."}, status=400)

    mapped_to = emp
    if is_admin:
        raw = body.get("mapped_to_id")
        if raw in (None, "", 0):
            mapped_to = None
        else:
            mapped_to = Employee.objects.filter(pk=raw, active=True).first()

    dob = None
    raw_dob = str(body.get("date_of_birth") or "").strip()
    if raw_dob:
        try:
            dob = date.fromisoformat(raw_dob)
        except ValueError:
            return JsonResponse({"ok": False, "error": "Date of birth must be YYYY-MM-DD."}, status=400)

    client = Client.objects.create(
        name=name[:255],
        phone=phone[:15],
        email=str(body.get("email") or "").strip()[:254] or None,
        pan=str(body.get("pan") or "").strip().upper()[:20] or None,
        address=str(body.get("address") or "").strip() or None,
        date_of_birth=dob,
        mapped_to=mapped_to,
        status="Mapped" if mapped_to else "Unmapped",
    )
    return JsonResponse({"ok": True, "id": client.id})


# ── Screen 12: Incentive rules + campaigns (admin builders, read snapshots;
#    mutations reuse the existing web AJAX endpoints) ────────────────────────

from ..models import Campaign, IncentiveRule  # noqa: E402


@login_required
@require_GET
def app_incentives(request):
    denied = _team_forbidden(request)
    if denied:
        return denied
    rules = []
    used_product_ids = set()
    used_names = set()
    for r in IncentiveRule.objects.select_related("product_ref").prefetch_related("slabs"):
        if r.product_ref_id:
            used_product_ids.add(r.product_ref_id)
        used_names.add((r.product or "").strip().lower())
        rules.append({
            "id": r.id,
            "product": r.product_ref.name if r.product_ref_id else r.product,
            "unit_amount": _money(r.unit_amount),
            "points_per_unit": _money(r.points_per_unit),
            "active": r.active,
            "slabs": [
                {"id": s.id, "threshold": _money(s.threshold), "payout": _money(s.payout), "label": s.label}
                for s in r.slabs.all()
            ],
        })
    available = [
        {"id": p.id, "name": p.name}
        for p in Product.objects.filter(
            is_active=True, archived_at__isnull=True,
            domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
        ).order_by("display_order", "name")
        if p.id not in used_product_ids and (p.name or "").strip().lower() not in used_names
    ]
    return JsonResponse({"rules": rules, "available_products": available})


@login_required
@require_GET
def app_campaigns(request):
    denied = _team_forbidden(request)
    if denied:
        return denied
    campaigns = []
    for c in Campaign.objects.prefetch_related("products__product_ref", "products__slabs"):
        campaigns.append({
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "start_date": c.start_date.isoformat(),
            "end_date": c.end_date.isoformat(),
            "is_active": c.is_active,
            "products": [
                {
                    "id": cp.id,
                    "product_name": cp.product_ref.name if cp.product_ref_id else "",
                    "benefit_type": cp.benefit_type,
                    "unit_amount": _money(cp.unit_amount) if cp.unit_amount is not None else None,
                    "points_per_unit": _money(cp.points_per_unit) if cp.points_per_unit is not None else None,
                    "slabs": [
                        {"id": s.id, "threshold": _money(s.threshold), "payout": _money(s.payout), "label": s.label}
                        for s in cp.slabs.all()
                    ],
                }
                for cp in c.products.all()
            ],
        })
    products = [
        {"id": p.id, "name": p.name}
        for p in Product.objects.filter(
            is_active=True, archived_at__isnull=True,
            domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
        ).order_by("display_order", "name")
    ]
    return JsonResponse({"campaigns": campaigns, "products": products})


# ── Screen 13: Lead sheets (mobile card view over the dynamic columns) ──────

from ..models import LeadSheet, LeadSheetColumn, LeadSheetRecord  # noqa: E402
from .lead_records import (  # noqa: E402
    _accessible_sheets,
    _can_touch_record,
    _full_visibility,
    _round_robin,
    _sanitize_value,
    _visible_records,
)


@login_required
@require_GET
def app_sheets(request):
    sheets = list(
        _accessible_sheets(request).filter(archived=False).select_related("product", "owner__user")
    )
    counts = {}
    if sheets:
        for row in (
            LeadSheetRecord.objects.filter(sheet__in=sheets)
            .values("sheet_id").annotate(c=Count("id"))
        ):
            counts[row["sheet_id"]] = row["c"]
    return JsonResponse({
        "results": [
            {
                "id": s.id,
                "name": s.name,
                "product": s.product.name if s.product_id else "",
                "record_count": counts.get(s.id, 0),
                "is_private": s.is_private,
            }
            for s in sheets
        ],
    })


@login_required
@require_GET
def app_sheet_records(request, sheet_id):
    sheet = get_object_or_404(LeadSheet, pk=sheet_id)
    if not sheet.can_view(request.user):
        return JsonResponse({"ok": False, "error": "No access."}, status=403)

    columns = list(sheet.columns.all())
    qs = _visible_records(request, sheet).select_related("assigned_to__user", "converted_client")

    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models.expressions import RawSQL
        qs = qs.annotate(_vtext=RawSQL("values::text", [])).filter(_vtext__icontains=q)

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    start, end = (page - 1) * _PAGE, page * _PAGE
    rows = list(qs.order_by("-created_at", "-id")[start:end + 1])

    def _row(r):
        vals = r.values or {}
        return {
            "id": r.id,
            "values": {c.field_key: vals.get(c.field_key, "") for c in columns},
            "tags": r.tags or [],
            "assigned_to": (
                r.assigned_to.user.get_full_name() or r.assigned_to.user.username
            ) if r.assigned_to_id and r.assigned_to.user_id else "",
            "converted": bool(r.converted_client_id),
        }

    return JsonResponse({
        "sheet": {"id": sheet.id, "name": sheet.name, "can_edit": sheet.can_edit(request.user)},
        "columns": [
            {
                "key": c.field_key, "name": c.name, "type": c.type,
                "options": c.options or [], "required": c.required,
            }
            for c in columns
        ],
        "results": [_row(r) for r in rows[:_PAGE]],
        "has_more": len(rows) > _PAGE,
        "page": page,
    })


@login_required
@require_POST
def app_sheet_record_save(request, sheet_id):
    """Create (no record_id) or update (record_id) a row's values."""
    sheet = get_object_or_404(LeadSheet, pk=sheet_id)
    if not sheet.can_edit(request.user):
        return JsonResponse({"ok": False, "error": "No edit permission."}, status=403)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    columns = list(sheet.columns.all())
    raw_values = body.get("values") or {}
    record_id = body.get("record_id")

    if record_id:
        record = get_object_or_404(LeadSheetRecord, pk=record_id, sheet=sheet)
        if not _can_touch_record(request, sheet, record):
            return JsonResponse({"ok": False, "error": "This row isn't assigned to you."}, status=403)
        merged = dict(record.values or {})
        for c in columns:
            if c.field_key in raw_values:
                merged[c.field_key] = _sanitize_value(c, raw_values.get(c.field_key))
        record.values = merged
        record.updated_by = request.user
        record.save(update_fields=["values", "updated_by", "updated_at"])
    else:
        values = {c.field_key: _sanitize_value(c, raw_values.get(c.field_key, "")) for c in columns}
        missing = [c.name for c in columns if c.required and not values.get(c.field_key)]
        if missing:
            return JsonResponse({"ok": False, "error": f"Required: {', '.join(missing)}."}, status=400)
        assignee = next(_round_robin(sheet, 1), None)
        record = LeadSheetRecord.objects.create(
            sheet=sheet, values=values, assigned_to=assignee,
            created_by=request.user, updated_by=request.user,
        )
    sheet.save(update_fields=["updated_at"])
    return JsonResponse({"ok": True, "id": record.id})


# ── Screen 14: Native login ──────────────────────────────────────────────────

from django.contrib.auth import authenticate as _authenticate, login as _auth_login  # noqa: E402
from django.core.cache import cache as _cache  # noqa: E402
from django.middleware.csrf import get_token as _get_csrf_token  # noqa: E402
from django.views.decorators.csrf import csrf_exempt as _csrf_exempt  # noqa: E402


@_csrf_exempt
@require_POST
def app_login(request):
    """Native login. csrf-exempt because it's the pre-session entry point
    (no authenticated session exists yet to protect). Reuses the same
    per-IP+username lockout as the web login."""
    from .auth import LOGIN_MAX_ATTEMPTS, LOGIN_LOCKOUT_SECONDS, _login_lockout_key

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    key = _login_lockout_key(request, username)
    fails = _cache.get(key, 0)
    if fails >= LOGIN_MAX_ATTEMPTS:
        return JsonResponse(
            {"ok": False, "error": "Too many failed attempts. Try again in 15 minutes."},
            status=429,
        )

    user = _authenticate(request, username=username, password=password)
    if user is None:
        _cache.set(key, fails + 1, LOGIN_LOCKOUT_SECONDS)
        return JsonResponse({"ok": False, "error": "Invalid username or password."}, status=401)

    emp = getattr(user, "employee", None)
    role = emp.role if (emp and emp.role) else ("admin" if (user.is_superuser or user.is_staff) else None)
    if role is None:
        return JsonResponse(
            {"ok": False, "error": "No employee role mapped. Contact an administrator."},
            status=403,
        )

    _cache.delete(key)
    _auth_login(request, user)          # sets sessionid on the response
    _get_csrf_token(request)            # forces csrftoken cookie onto the response
    return JsonResponse({
        "ok": True,
        "role": role,
        "name": user.get_full_name() or user.username,
    })


# ── Reports hub: monthly report + past performance ───────────────────────────

from calendar import month_name as _month_name  # noqa: E402


def _reports_allowed(request):
    """Admin/superuser, or manager with employee-performance access."""
    emp = _emp(request)
    if request.user.is_superuser or (emp and emp.role == "admin"):
        return True, True  # allowed, firm_wide
    if emp and emp.role == "manager":
        return bool(get_manager_access().allow_employee_performance), True
    return True, False  # plain employee: allowed, own-scope only


@login_required
@require_GET
def app_report_monthly(request):
    """Product-wise + per-employee business for a chosen month."""
    allowed, firm_wide = _reports_allowed(request)
    if not allowed:
        return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)

    today = timezone.localdate()
    try:
        sel_month = int(request.GET.get("month", today.month))
        sel_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        sel_month, sel_year = today.month, today.year
    if not 1 <= sel_month <= 12:
        sel_month = today.month

    emp = _emp(request)
    approved = Sale.objects.filter(status=Sale.STATUS_APPROVED, date__year=sel_year, date__month=sel_month)
    if not firm_wide:
        approved = approved.filter(employee=emp)

    products = [
        {"name": r["product"] or "Other", "amount": _money(r["t"]), "count": r["n"]}
        for r in approved.values("product").annotate(t=Sum("amount"), n=Count("id")).order_by("-t")
    ]
    tot = approved.aggregate(amount=Sum("amount"), points=Sum("points"), n=Count("id"))
    data = {
        "firm_wide": firm_wide,
        "month": sel_month,
        "year": sel_year,
        "month_label": _month_name[sel_month],
        "products": products,
        "total_amount": _money(tot["amount"]),
        "total_points": _money(tot["points"]),
        "total_count": tot["n"] or 0,
        "months": [{"value": i, "label": _month_name[i]} for i in range(1, 13)],
        "years": list(range(today.year - 3, today.year + 1)),
    }
    if firm_wide:
        data["employees"] = [
            {
                "name": r["employee__user__first_name"] or r["employee__user__username"] or "—",
                "amount": _money(r["amount"]),
                "points": _money(r["points"]),
            }
            for r in approved.values(
                "employee__user__username", "employee__user__first_name"
            ).annotate(amount=Sum("amount"), points=Sum("points")).order_by("-amount")
        ]
    return JsonResponse(data)


@login_required
@require_GET
def app_report_past(request):
    """Last 12 months business trend (amount + points), own or firm/employee."""
    allowed, firm_wide = _reports_allowed(request)
    if not allowed:
        return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)

    today = timezone.localdate()
    emp = _emp(request)

    # Optional employee drill-down (firm-wide viewers only)
    target_emp = None
    if firm_wide:
        raw = request.GET.get("employee_id")
        if raw and raw != "all":
            target_emp = Employee.objects.filter(pk=raw).first()
    else:
        target_emp = emp

    # Build last 12 (year, month) oldest→newest
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()

    trend = []
    for (yy, mm) in months:
        f = {"status": Sale.STATUS_APPROVED, "date__year": yy, "date__month": mm}
        if target_emp:
            f["employee"] = target_emp
        agg = Sale.objects.filter(**f).aggregate(amount=Sum("amount"), points=Sum("points"))
        trend.append({
            "label": _month_name[mm][:3],
            "year": yy,
            "amount": _money(agg["amount"]),
            "points": _money(agg["points"]),
        })

    max_amt = max((t["amount"] for t in trend), default=0.0)
    for t in trend:
        t["percent"] = round((t["amount"] / max_amt) * 100, 1) if max_amt else 0.0

    data = {
        "firm_wide": firm_wide,
        "scope_name": (
            (target_emp.user.get_full_name() or target_emp.user.username)
            if target_emp and target_emp.user_id else ("Whole firm" if firm_wide else "You")
        ),
        "trend": trend,
        "total_amount": _money(sum(t["amount"] for t in trend)),
        "total_points": _money(sum(t["points"] for t in trend)),
    }
    if firm_wide:
        data["employees"] = [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ]
    return JsonResponse(data)

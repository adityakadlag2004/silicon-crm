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


@login_required
@require_GET
def app_followups(request):
    emp = _emp(request)
    if emp is None:
        return JsonResponse({"pending": [], "done": []})
    now = timezone.now()
    pending = CallFollowUp.objects.filter(
        employee=emp, status=CallFollowUp.STATUS_PENDING
    ).select_related("client").order_by("scheduled_at")[:100]
    done = CallFollowUp.objects.filter(
        employee=emp, status__in=[CallFollowUp.STATUS_DONE, CallFollowUp.STATUS_DISMISSED]
    ).select_related("client").order_by("-completed_at")[:15]
    return JsonResponse({
        "pending": [_fu_row(f, now) for f in pending],
        "done": [_fu_row(f, now) for f in done],
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

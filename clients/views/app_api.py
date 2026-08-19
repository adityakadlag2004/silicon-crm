"""JSON API for the native Android screens (mobile/NATIVE_MIGRATION.md).

Same auth model as views/calls.py: the native app sends the WebView's
session cookie + X-CSRFToken header. One endpoint set is added here per
converted screen.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .. import permissions
from ..models import (
    CallFollowUp,
    Client,
    Employee,
    Notification,
    Product,
    Renewal,
    Sale,
)
from ..services import incentives as _incentives
from ..services import calls as calls_service
from ..services import sales as sales_service
from ..utils.phone_utils import digits10
from .helpers import get_manager_access, name_words_q
from .reports import business_overview_data


def _emp(request):
    return getattr(request.user, "employee", None)


def _is_admin(request):
    return permissions.is_admin(request.user)


def _money(value):
    """Decimal → float for JSON (display-only figures)."""
    return float(value or 0)


@login_required
@require_GET
def app_me(request):
    """Who is signed in — three cheap fields.

    Menu, Tasks and Reports each used to call the full `app_dashboard` (ten
    aggregate queries for an admin) just to read `role`. This is what they
    actually wanted.
    """
    emp = _emp(request)
    return JsonResponse({
        "role": "admin" if _is_admin(request) else (emp.role if emp else "unknown"),
        "name": request.user.get_full_name() or request.user.username,
        "employee_id": emp.id if emp else None,
        "unread_notifications": Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count(),
        # Drives the Calls tab badge, so a due call is visible without
        # opening the screen. Call follow-ups only — a lead or claim follow-up
        # is a Task now and badges the Tasks tab.
        "overdue_followups": CallFollowUp.objects.filter(
            employee=emp, status=CallFollowUp.STATUS_PENDING,
            scheduled_at__lte=timezone.now(),
        ).count() if emp else 0,
    })


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
        # Drives the app's "complete your profile" nudge (web has its own).
        "profile_percent": emp.profile_completeness if emp else 100,
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
    }
    # `recent_sales` used to be built here and thrown away: no app screen ever
    # rendered it. That was a join + 8 rows on every dashboard load, four times
    # per app open. Removed — Menu → All Sales shows the same thing, paged.

    if is_admin:
        data["pending_approvals"] = Sale.objects.filter(status=Sale.STATUS_PENDING).count()
        data["unmapped_clients"] = Client.objects.filter(mapped_to__isnull=True).count()

        # Today's team leaderboard (approved business today, top 6)
        data["leaderboard_today"] = [
            {
                "name": r["employee__user__first_name"] or r["employee__user__username"] or "—",
                "amount": _money(r["amount"]),
                "count": r["n"],
            }
            for r in Sale.objects.filter(status=Sale.STATUS_APPROVED, date=today)
            .values("employee__user__username", "employee__user__first_name")
            .annotate(amount=Sum("amount"), n=Count("id")).order_by("-amount")[:6]
        ]

        # Month-to-date business per product CATEGORY (1st of month → today).
        # Sub-products fold into their parent so the board shows one row per
        # category, not dozens of sub-product rows.
        month_start = today.replace(day=1)
        sub_to_cat = dict(
            Product.objects.filter(parent__isnull=False).values_list("name", "parent__name")
        )
        mtd_buckets = {}
        for r in Sale.objects.filter(
            status=Sale.STATUS_APPROVED, date__gte=month_start, date__lte=today
        ).values("product").annotate(t=Sum("amount"), n=Count("id")):
            name = sub_to_cat.get(r["product"], r["product"]) or "Other"
            b = mtd_buckets.setdefault(name, {"name": name, "amount": Decimal("0"), "count": 0})
            b["amount"] += r["t"] or Decimal("0")
            b["count"] += r["n"]
        data["product_mtd"] = [
            {"name": b["name"], "amount": _money(b["amount"]), "count": b["count"]}
            for b in sorted(mtd_buckets.values(), key=lambda x: x["amount"], reverse=True)
        ]

        # Team call activity today (within the office-hours window)
        from ..models import CallLogEntry, CallTrackingSettings
        cfg = CallTrackingSettings.current()
        ca = CallLogEntry.objects.filter(
            started_at__date=today,
            started_at__time__gte=cfg.work_start,
            started_at__time__lt=cfg.work_end,
            started_at__week_day__in=cfg.work_week_days_django(),
        ).aggregate(
            calls=Count("id"),
            connected_count=Count("id", filter=Q(connected=True)),
            talk_seconds=Sum("duration_seconds", filter=Q(connected=True)),
            serious=Count("id", filter=Q(duration_seconds__gt=_SERIOUS_CALL_SECONDS)),
        )
        data["team_calls_today"] = {
            "calls": ca["calls"] or 0,
            "connected": ca["connected_count"] or 0,
            "talk_minutes": round((ca["talk_seconds"] or 0) / 60, 1),
            "serious": ca["serious"] or 0,
        }

    # ── Gamified employee dashboard (non-admin) ──
    if emp is not None and not is_admin:
        salary = float(emp.salary or 0)
        earned = float(month_agg["points"] or 0)  # incentive points ≈ ₹ value (1:1)
        data["earnings"] = {
            "salary": salary,
            "earned": earned,
            "percent": round(earned / salary * 100, 1) if salary > 0 else None,
            "surplus": max(0.0, earned - salary),
            "remaining": max(0.0, salary - earned),
            "justified": salary > 0 and earned >= salary,
        }

        # Own month-to-date business per product
        month_start = today.replace(day=1)
        data["product_mtd"] = [
            {"name": r["product"] or "Other", "amount": _money(r["t"]), "count": r["n"]}
            for r in Sale.objects.filter(
                employee=emp, status=Sale.STATUS_APPROVED,
                date__gte=month_start, date__lte=today,
            ).values("product").annotate(t=Sum("amount"), n=Count("id")).order_by("-t")
        ]

        # Live campaigns the employee can earn extra on
        from ..models import Campaign, CampaignProduct
        camps = Campaign.objects.filter(
            is_active=True, start_date__lte=today, end_date__gte=today,
        ).prefetch_related("products__product_ref")
        active_campaigns = []
        for c in camps:
            products = []
            for cp in c.products.all():
                products.append({
                    "product": cp.product_ref.name if cp.product_ref_id else "",
                    "benefit_type": cp.benefit_type,
                    "unit_amount": _money(cp.unit_amount),
                    "points_per_unit": float(cp.points_per_unit or 0),
                })
            if products:
                active_campaigns.append({
                    "name": c.name,
                    "ends": c.end_date.strftime("%d %b"),
                    "products": products,
                })
        data["active_campaigns"] = active_campaigns

    # ── SPANCO pipeline ──
    # The two things both web dashboards lead with and the app had neither of:
    # where the pipeline stands, and the live leads nobody has dated. Tasks and
    # call follow-ups stay off this screen on purpose — they have their own.
    # `_lead_qs` scopes it (an employee sees only their own); the interests
    # prefetch is dropped, nothing here renders it.
    lead_qs = _lead_qs(request).prefetch_related(None)
    standing = lead_service.stage_counts(lead_qs.filter(is_discarded=False))
    data["pipeline"] = {
        "stages": [
            {
                "stage": stage,
                "label": label,
                "count": standing.get(stage, 0),
                "hot": stage in Lead.STAGE_HOT,
            }
            for stage, label in Lead.STAGE_CHOICES
        ],
        "live": sum(standing.values()),
        # One page per stage from Approach on — the screen is swiped through
        # stage by stage, and each lead says on its own card whether it is
        # being chased. A separate "needs you" list beside it would print the
        # same leads twice and hide the chased ones entirely.
        "board": [
            {
                "stage": page["stage"],
                "label": page["label"],
                "count": page["count"],
                "unchased": page["unchased"],
                "has_more": page["has_more"],
                "leads": [
                    {
                        "id": r["lead"].id,
                        "name": r["lead"].customer_name,
                        "phone": r["lead"].phone or "",
                        "owner": r["owner"],
                        "days_in_stage": r["days_in_stage"],
                        "followups": r["followups"],
                        "next_followup": (r["next_followup"].isoformat()
                                          if r["next_followup"] else None),
                        "stalled": r["stalled"],
                    }
                    for r in page["rows"]
                ],
            }
            for page in lead_service.board(lead_qs)
        ],
    }

    return JsonResponse(data)


# ── Screen 2: Add Sale ───────────────────────────────────────────────────────

def _product_flags(p):
    # Parent-aware: a sub-product of Health/Life reports the same as its parent.
    return p.is_health, p.is_insurance


@login_required
@require_GET
def app_sale_meta(request):
    """Products + (for admins) employee list for the Add Sale screen."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    rows = list(Product.objects.filter(
        is_active=True, archived_at__isnull=True,
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).select_related("parent").order_by("display_order", "name"))
    ids = {p.id for p in rows}
    kids = {}
    for p in rows:
        if p.parent_id in ids:
            kids.setdefault(p.parent_id, []).append(p)

    # PPT options per product (PPT-priced life plans). Advisor and MDRT share the
    # same PPT set, so one list per product suffices for the picker.
    from ..models import PlanPptRate
    from ..models.catalog import _ppt_sort_key
    ppt_by_prod = {}
    for r in PlanPptRate.objects.filter(product_id__in=ids, designation=PlanPptRate.DESIG_ADVISOR):
        ppt_by_prod.setdefault(r.product_id, []).append(r.ppt)
    for k, v in ppt_by_prod.items():
        ppt_by_prod[k] = sorted(v, key=lambda p: _ppt_sort_key(type("R", (), {"ppt": p})()))

    # Top-level products, each carrying its sub-products. The app shows the
    # second picker (mandatory) only when `subproducts` is non-empty; otherwise
    # the main product is booked as before. Insurance flags are parent-level,
    # so a sub-product inherits its parent's cover/policy fields.
    products = []
    for p in rows:
        if p.parent_id in ids:
            continue
        is_health, is_insurance = _product_flags(p)
        products.append({
            "id": p.id, "name": p.name,
            "is_health": is_health, "is_insurance": is_insurance,
            "ppt_options": ppt_by_prod.get(p.id, []),
            "subproducts": [
                {"id": c.id, "name": c.name, "ppt_options": ppt_by_prod.get(c.id, [])}
                for c in kids.get(p.id, [])
            ],
        })
    data = {
        "is_admin": is_admin,
        "employee_id": emp.id if emp else None,
        "products": products,
        # Everyone may credit a sale to a colleague (a non-admin's sale still
        # lands pending), so the picker list is not admin-only.
        "employees": [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ],
    }
    if is_admin:
        # FYC (sale margin %) per plan+PPT at the active designation — admin-only,
        # matching the web (non-admins never receive commission figures).
        from ..models import FirmSettings
        mdrt = FirmSettings.get_settings().is_mdrt_active()
        desig = PlanPptRate.DESIG_MDRT if mdrt else PlanPptRate.DESIG_ADVISOR
        fyc = {}
        for r in PlanPptRate.objects.filter(
            product_id__in=ids, designation=desig
        ).exclude(fyc__isnull=True).select_related("product"):
            fyc.setdefault(r.product.name, {})[r.ppt] = str(r.fyc)
        data["ppt_fyc"] = fyc
        data["mdrt_active"] = mdrt
    return JsonResponse(data)


@login_required
@require_POST
def app_sale_create(request):
    """Create a sale. Mirrors the web add_sale rules: anyone may attribute the
    sale to another employee; admin sales auto-approve, everyone else's pend."""
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
    # If the chosen product is a category with sub-products, a sub-product must
    # be picked (its own margin applies) — the client should send that id.
    if product.children.filter(
        is_active=True, archived_at__isnull=True,
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).exists():
        return JsonResponse({"ok": False, "error": "Select a sub-product."}, status=400)

    # PPT is mandatory for PPT-priced life plans (its FYC is the sale margin).
    ppt = (body.get("ppt") or "").strip()
    if product.has_ppt_rates:
        if not ppt:
            return JsonResponse({"ok": False, "error": "Select the Premium Paying Term (PPT)."}, status=400)
        if ppt not in set(product.ppt_rates.values_list("ppt", flat=True)):
            return JsonResponse({"ok": False, "error": "Invalid PPT for this plan."}, status=400)
    else:
        ppt = ""

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
    if product.is_health and not policy_type:
        return JsonResponse({"ok": False, "error": "Select Port or Fresh for Health Insurance."}, status=400)

    # Policy date + number: mandatory for Health/Life, meaningless otherwise.
    # Same rule as the web sale forms — the sale date is the approval day, and
    # renewals must be measured from the policy's own commencement date.
    policy_date, policy_number = None, ""
    if product.is_insurance:
        # App builds before v4.23 have no such fields and omit the keys
        # entirely. Telling those users to "enter the policy date" points at a
        # box that doesn't exist on their screen — tell them to update instead.
        if "policy_date" not in body and "policy_number" not in body:
            return JsonResponse(
                {"ok": False, "error": (
                    "Update the app to add insurance sales — this version can't record "
                    "the policy date and number. Open the app again to get the update, "
                    "or add the sale on the website."
                )},
                status=400,
            )
        try:
            policy_date = date.fromisoformat(str(body.get("policy_date") or ""))
        except ValueError:
            return JsonResponse(
                {"ok": False, "error": "Enter the policy date from the policy document (YYYY-MM-DD)."},
                status=400,
            )
        policy_number = str(body.get("policy_number") or "").strip()[:60]
        if not policy_number:
            return JsonResponse(
                {"ok": False, "error": "Enter the policy number from the policy document."},
                status=400,
            )

    # Multiyear + EMI apply to Health only (EMI needs a multiyear term).
    try:
        policy_years = int(body.get("policy_years") or 1)
        emi_months = int(body.get("emi_months") or 0)
    except (TypeError, ValueError):
        policy_years, emi_months = 1, 0
    if product.is_health:
        policy_years = policy_years if policy_years in (1, 2, 3) else 1
        emi_months = emi_months if emi_months in (0, 5, 8, 11) else 0
        if policy_years <= 1:
            emi_months = 0
    else:
        policy_years, emi_months = 1, 0

    sale_emp = emp
    if body.get("employee_id"):
        sale_emp = Employee.objects.filter(pk=body.get("employee_id"), active=True).first() or emp
    if sale_emp is None:
        return JsonResponse({"ok": False, "error": "Your account is not mapped to an employee."}, status=403)

    sale = Sale(
        client=client, employee=sale_emp, product=product.name, product_ref=product,
        amount=amount, cover_amount=cover_amount, policy_type=policy_type, ppt=ppt,
        policy_date=policy_date, policy_number=policy_number,
        policy_years=policy_years, emi_months=emi_months,
    )

    # Same client + product + amount inside the window is nearly always the
    # same sale entered twice. The app asks, then re-posts with the flag —
    # a real second sale still goes through.
    if not body.get("confirm_duplicate"):
        dup = sales_service.find_duplicate(sale)
        if dup is not None:
            return JsonResponse({
                "ok": False,
                "duplicate": True,
                "existing": {
                    "id": dup.id,
                    "date": dup.date.isoformat() if dup.date else "",
                    "product": dup.product,
                    "amount": str(dup.amount),
                    "employee": (dup.employee.user.username if dup.employee_id and dup.employee.user_id else ""),
                    "status": dup.status,
                },
                "error": (
                    f"{dup.client.name} already has a {dup.product} sale of ₹{dup.amount:,.0f} "
                    f"on {dup.date}. Add it again only if it is a genuine second sale."
                ),
            }, status=409)

    sales_service.finalize_new_sale(sale, request.user, auto_approve=is_admin)
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
            name_words_q("name", q) | Q(phone__icontains=q)
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

def _fu_row(fu, now, last_calls=None, names=None):
    # The ringing alarm shows `client` and falls back to the raw number
    # (FollowupAlarmScheduler.syncFromPending). A follow-up links its client
    # once, at creation, so anything that was a lead — or became a client
    # afterwards — rang as a bare phone number forever. Resolving here fixes
    # the whole fleet without an app release.
    who = fu.client.name if fu.client_id else ""
    if not who and names:
        who = (names.get(_fu_digits(fu.phone)) or ("", "", None))[0]
    return {
        "id": fu.id,
        "kind": "call",
        "phone": fu.phone,
        "client": who,
        "client_id": fu.client_id,
        "scheduled_at": timezone.localtime(fu.scheduled_at).strftime("%d %b, %I:%M %p"),
        # Epoch millis so the app can schedule an exact on-device alarm.
        "scheduled_at_ms": int(fu.scheduled_at.timestamp() * 1000),
        "overdue": fu.scheduled_at <= now,
        "note": fu.note or "",
        "status": fu.status,
        "outcome": fu.outcome,
        "outcome_label": fu.get_outcome_display() if fu.outcome else "",
        # How many times this number has been chased (1 = first attempt).
        "attempts": fu.attempts,
        # "2 days ago · 3m 12s" for the last call to this number, "" if never.
        "last_call": (last_calls or {}).get(_fu_digits(fu.phone), ""),
    }


# The same key every other matcher uses. It has to strip the Excel float tail
# too ("9423440791.0"), which naive digit-stripping read as 4234407910.
_fu_digits = digits10


def _last_call_map(emp, followups):
    """{last-10-digits: "3 days ago · 2m 05s"} for the numbers in `followups`.

    One query for the whole list: what a card needs to open the call is when
    this number was last reached and for how long, and the call log already
    knows it.
    """
    from ..models import CallLogEntry

    wanted = {_fu_digits(f.phone) for f in followups if _fu_digits(f.phone)}
    if not emp or not wanted:
        return {}
    out = {}
    # Newest first, so the first row seen per number is the last call.
    for entry in CallLogEntry.objects.filter(employee=emp).order_by("-started_at")[:500]:
        key = _fu_digits(entry.phone)
        if key not in wanted or key in out:
            continue
        days = (timezone.localdate() - timezone.localtime(entry.started_at).date()).days
        when = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
        if not entry.connected:
            out[key] = f"Last {when}: not connected"
        else:
            secs = entry.duration_seconds
            length = f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"
            out[key] = f"Last {when} · {length}"
    return out


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
        started_at__week_day__in=cfg.work_week_days_django(),
    )
    agg = qs.aggregate(
        # Outgoing-only, same definition as the web/app Call Analytics
        # "Dialed"/"Connected" columns so all surfaces agree.
        calls=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING)),
        connected_count=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING, connected=True)),
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
    pending = list(
        CallFollowUp.objects.filter(
            employee=emp, status=CallFollowUp.STATUS_PENDING
        ).select_related("client").order_by("scheduled_at")[:500]
    )
    # Closed today, so the screen can show what was worked, not just what's left.
    done = CallFollowUp.objects.filter(
        employee=emp,
        status__in=[CallFollowUp.STATUS_DONE, CallFollowUp.STATUS_DISMISSED],
        completed_at__date=timezone.localdate(),
    ).select_related("client").order_by("-completed_at")[:50]
    last_calls = _last_call_map(emp, pending)
    names = calls_service.caller_names([f.phone for f in pending if not f.client_id])
    rows = [_fu_row(f, now, last_calls, names) for f in pending]
    done = list(done)
    done_names = calls_service.caller_names([f.phone for f in done if not f.client_id])
    return JsonResponse({
        "pending": rows,
        "done_today": [_fu_row(f, now, names=done_names) for f in done],
        "overdue": sum(1 for r in rows if r["overdue"]),
        "stats": _today_call_stats(emp),
        "outcomes": [
            {"key": k, "label": v} for k, v in CallFollowUp.OUTCOME_CHOICES
        ],
    })


@login_required
@require_POST
def app_followup_action(request, followup_id):
    emp = _emp(request)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = {}
    action = body.get("action")

    if body.get("kind") == "lead":
        # Pre-v4.32 apps still send these. Lead follow-ups are Tasks now and
        # are worked on the Tasks screen; say so rather than 404.
        return JsonResponse(
            {"ok": False, "error": "Lead follow-ups are tasks now — update the app."},
            status=410)

    fu = get_object_or_404(CallFollowUp, pk=followup_id)
    if not (_is_admin(request) or (emp and fu.employee_id == emp.id)):
        return JsonResponse({"ok": False, "error": "Not your follow-up."}, status=403)

    if action == "done":
        fu.status = CallFollowUp.STATUS_DONE
        fu.completed_at = timezone.now()
        outcome = body.get("outcome") or ""
        if outcome in dict(CallFollowUp.OUTCOME_CHOICES):
            fu.outcome = outcome
        fu.save(update_fields=["status", "completed_at", "outcome"])
    elif action == "note":
        # A note was write-once on every surface — a follow-up you can't
        # correct is one you stop trusting.
        fu.note = str(body.get("note") or "").strip()[:255]
        fu.save(update_fields=["note"])
    elif action == "dismiss":
        fu.status = CallFollowUp.STATUS_DISMISSED
        fu.completed_at = timezone.now()
        fu.save(update_fields=["status", "completed_at"])
    elif action in ("snooze", "reschedule"):
        # snooze = the old fixed +1h; reschedule = an exact moment picked on
        # the phone. Both re-arm the reminder.
        if action == "reschedule":
            from .calls import parse_custom_at
            scheduled, err = parse_custom_at(body.get("at"))
            if err:
                return JsonResponse({"ok": False, "error": err}, status=400)
        else:
            scheduled = timezone.now() + timedelta(hours=1)
        fu.scheduled_at = scheduled
        fu.status = CallFollowUp.STATUS_PENDING
        fu.reminded = False
        fu.save(update_fields=["scheduled_at", "status", "reminded"])
    else:
        return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def app_followups_push_overdue(request):
    """End-of-day cleanup: move every overdue follow-up to a picked moment.

    Twenty rows that all say DUE are as good as no list at all; rescheduling
    them one at a time is why they get left to rot instead.
    """
    emp = _emp(request)
    if emp is None:
        return JsonResponse({"ok": False, "error": "No employee account."}, status=403)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = {}

    from .calls import parse_custom_at

    scheduled, err = parse_custom_at(body.get("at"))
    if err:
        return JsonResponse({"ok": False, "error": err}, status=400)
    now = timezone.now()
    moved = CallFollowUp.objects.filter(
        employee=emp, status=CallFollowUp.STATUS_PENDING, scheduled_at__lte=now
    ).update(scheduled_at=scheduled, reminded=False)
    return JsonResponse({"ok": True, "moved": moved, "message": f"Moved {moved} follow-ups"})


# ── Screen 5: Sales list + approvals ─────────────────────────────────────────

@login_required
@require_GET
def app_sales(request):
    emp = _emp(request)
    is_admin = _is_admin(request)
    is_manager = bool(emp and emp.role == "manager")
    access = get_manager_access() if is_manager else None
    can_approve = permissions.can(request.user, "approve_sales")

    qs = Sale.objects.select_related("client", "employee__user").order_by("-date", "-created_at")
    if not permissions.can(request.user, "view_all_sales"):
        qs = qs.filter(employee=emp) if emp else qs.none()

    status = request.GET.get("status", "")
    if status in (Sale.STATUS_PENDING, Sale.STATUS_APPROVED, Sale.STATUS_REJECTED):
        qs = qs.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            name_words_q("client__name", q) | Q(product__icontains=q)
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
        "can_delete": is_admin,
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
    is_admin = _is_admin(request)
    if not permissions.can(request.user, "approve_sales"):
        return JsonResponse({"ok": False, "error": "No permission."}, status=403)

    sale = get_object_or_404(Sale, pk=sale_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = {}
    action = body.get("action")

    if action == "delete":
        # Admins/superusers only (matches the web delete_sale admin path).
        if not is_admin:
            return JsonResponse({"ok": False, "error": "Only an admin can delete a sale."}, status=403)
        sales_service.delete_sale(sale, request.user)
        return JsonResponse({"ok": True, "deleted": True})

    if action == "approve":
        sales_service.approve_sale(sale, request.user)
    elif action == "reject":
        sales_service.reject_sale(sale, request.user, body.get("reason"))
    else:
        return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)

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
            name_words_q("client__name", q) | Q(client__phone__icontains=q)
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

    # Link to the Insurance Tracker exactly as the web form does: the existing
    # policy the user ticked, or a new one from the number they typed. Without
    # this, every renewal entered on a phone was an orphan row and the old book
    # never got captured. Best-effort — a tracker hiccup must not lose the
    # renewal that was just saved.
    policy_id = None
    try:
        from ..services import insurance_sync
        policy = insurance_sync.link_renewal_to_policy(
            renewal,
            selected_policy_id=(str(body.get("policy_id") or "").strip() or None),
            new_policy_number=str(body.get("policy_number") or "").strip(),
        )
        policy_id = policy.id if policy else None
    except Exception:
        pass
    return JsonResponse({"ok": True, "id": renewal.id, "policy_id": policy_id})


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
    """Mark all as read, or just one when `id` is supplied.

    Opening a notification is the clearest possible signal that it has been
    read — the app used to leave it bold forever unless the user found "Mark
    all read", so the unread count only ever grew.
    """
    qs = Notification.objects.filter(recipient=request.user, is_read=False)
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    if body.get("id"):
        qs = qs.filter(pk=body["id"])
    qs.update(is_read=True)
    return JsonResponse({
        "ok": True,
        "unread": Notification.objects.filter(recipient=request.user, is_read=False).count(),
    })


# ── Screen 9: Leads pipeline (SPANCO) ────────────────────────────────────────

from django.db import transaction  # noqa: E402

from ..models import Lead, LeadInterest, LeadRemark, Task  # noqa: E402
from ..services import followups as followups_service  # noqa: E402
from ..services import leads as lead_service  # noqa: E402


def _lead_qs(request):
    """Same scoping as the web pipeline: employees see only their own leads."""
    emp = _emp(request)
    qs = Lead.objects.select_related("assigned_to__user").prefetch_related("interests__product")
    if emp and emp.role == "employee":
        qs = qs.filter(assigned_to=emp)
    return qs


def _can_assign_leads(request):
    return permissions.is_admin_or_manager(request.user)


def _interest_summary(lead):
    """"Health Insurance · SIP" — what this lead actually wants, or blank."""
    return " · ".join(i.label for i in lead.interests.all()[:3])


@login_required
@require_GET
def app_lead_meta(request):
    data = {
        "can_assign": _can_assign_leads(request),
        "stages": [
            {"value": v, "label": l, "help": Lead.STAGE_HELP[v]} for v, l in Lead.STAGE_CHOICES
        ],
        "products": [
            {"id": p.id, "name": p.name}
            for p in Product.objects.selectable().main().in_display_order()
        ],
    }
    if _can_assign_leads(request):
        data["employees"] = [
            {"id": e.id, "name": e.user.get_full_name() or e.user.username}
            for e in Employee.objects.filter(active=True).select_related("user").order_by("user__username")
        ]
    return JsonResponse(data)


@login_required
@require_GET
def app_leads(request):
    qs = _lead_qs(request)

    live = qs.filter(is_discarded=False)
    counts = {stage: 0 for stage, _ in Lead.STAGE_CHOICES}
    for row in live.values("stage").order_by().annotate(total=Count("id")):
        if row["stage"] in counts:
            counts[row["stage"]] = row["total"]
    counts["lost"] = qs.filter(is_discarded=True).count()

    stage = request.GET.get("stage", "")
    if stage == "lost":
        qs = qs.filter(is_discarded=True)
    else:
        qs = live
        if stage in dict(Lead.STAGE_CHOICES):
            qs = qs.filter(stage=stage)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            name_words_q("customer_name", q) | Q(phone__icontains=q) | Q(email__icontains=q)
        )

    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    start, end = (page - 1) * _PAGE, page * _PAGE
    rows = list(qs.order_by("-stage_changed_at", "-updated_at")[start:end + 1])

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
                "stage_label": l.get_stage_display(),
                "days_in_stage": l.days_in_stage,
                "is_discarded": l.is_discarded,
                "converted": bool(l.converted_client_id),
                "interests": _interest_summary(l),
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
    events = lead.stage_events.select_related("created_by")[:15]
    return JsonResponse({
        "id": lead.id,
        "name": lead.customer_name,
        "phone": lead.phone or "",
        "email": lead.email or "",
        "income": _money(lead.income) if lead.income is not None else None,
        "expenses": _money(lead.expenses) if lead.expenses is not None else None,
        "notes": lead.notes or "",
        "stage": lead.stage,
        "stage_label": lead.get_stage_display(),
        "stage_help": lead.stage_help,
        "next_stage": lead.next_stage or "",
        "days_in_stage": lead.days_in_stage,
        "is_discarded": lead.is_discarded,
        "lost_reason": lead.lost_reason,
        "converted_client_id": lead.converted_client_id,
        "can_convert": lead.stage == Lead.STAGE_ORDER and not lead.converted_client_id,
        "assigned_to": (
            lead.assigned_to.user.get_full_name() or lead.assigned_to.user.username
        ) if lead.assigned_to_id and lead.assigned_to.user_id else "",
        "interests": [
            {
                "id": i.id,
                "product_id": i.product_id,
                "label": i.label,
                "amount": _money(i.amount) if i.amount is not None else None,
                "note": i.note,
            }
            for i in lead.interests.all()
        ],
        "timeline": [
            {
                "from": e.from_label,
                "to": e.to_label,
                "note": e.note,
                "by": e.created_by.username if e.created_by_id else "",
                "at": timezone.localtime(e.created_at).strftime("%d %b, %I:%M %p"),
            }
            for e in events
        ],
        "remarks": [
            {
                "text": r.text,
                "by": r.created_by.username if r.created_by_id else "",
                "at": timezone.localtime(r.created_at).strftime("%d %b, %I:%M %p"),
            }
            for r in remarks
        ],
        # A follow-up is a Task, so these are tasks — the phone showed none of
        # them, which read as "nobody has scheduled anything" on the one screen
        # where you would go to schedule it.
        "followups": [
            {
                "id": t.pk,
                "note": _followup_note(t),
                "due": t.due_date.isoformat() if t.due_date else None,
                "due_time": t.due_time.strftime("%H:%M") if t.due_time else "",
                "status": t.status,
                "status_label": t.get_status_display(),
                "open": t.status in Task.OPEN_STATUSES,
                "assigned_to": (
                    t.assigned_to.user.get_full_name() or t.assigned_to.user.username
                ) if t.assigned_to_id and t.assigned_to.user_id else "",
            }
            for t in followups_service.for_source(followups_service.LEAD, lead.pk)[:20]
        ],
    })


def _followup_note(task):
    """What the follow-up is about, in one line.

    `followups._describe` puts the note first and the context lines after it,
    so the first line is the note — unless there wasn't one, in which case it
    is the first context line and there is nothing worth repeating.
    """
    first = (task.description or "").split("\n")[0].strip()
    return "" if first.startswith(("SPANCO stage:", "Phone:", "/clients/")) else first


@login_required
@require_POST
def app_lead_followup(request, lead_id):
    """Schedule a follow-up on a lead from the phone.

    Same one line of work the web form does — `followups.schedule` owns it, so
    this rings, lands on the calendar and shows on the Tasks screen exactly
    like every other follow-up.
    """
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = {}

    raw = (body.get("when") or "").strip()
    when = None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            when = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if when is None:
        return JsonResponse({"ok": False, "error": "Pick a follow-up date and time."},
                            status=400)

    task = followups_service.schedule(
        followups_service.LEAD, lead,
        timezone.make_aware(when, timezone.get_current_timezone()),
        note=(body.get("note") or "").strip(), actor=request.user)
    return JsonResponse({"ok": True, "id": task.pk})


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

    stage = str(body.get("stage") or Lead.STAGE_SUSPECT)
    if stage not in dict(Lead.STAGE_CHOICES):
        stage = Lead.STAGE_SUSPECT

    def _opt_decimal(key):
        raw = body.get(key)
        if raw in (None, ""):
            return None
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None

    with transaction.atomic():
        lead = Lead.objects.create(
            customer_name=name[:255],
            phone=str(body.get("phone") or "").strip()[:20],
            email=str(body.get("email") or "").strip()[:254],
            income=_opt_decimal("income"),
            expenses=_opt_decimal("expenses"),
            notes=str(body.get("notes") or "").strip(),
            assigned_to=assigned,
            created_by=request.user,
            stage=stage,
        )
        lead.stage_events.create(to_stage=stage, note="Lead created", created_by=request.user)
        # Products this lead needs — picked per lead, never seeded for them.
        for pid in body.get("product_ids") or []:
            product = Product.objects.selectable().main().filter(pk=pid).first()
            if product:
                LeadInterest.objects.get_or_create(lead=lead, product=product)
    return JsonResponse({"ok": True, "id": lead.id})


@login_required
@require_POST
def app_lead_stage(request, lead_id):
    """Move a lead along SPANCO."""
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    try:
        lead_service.set_stage(
            lead, str(body.get("stage") or ""), user=request.user,
            note=str(body.get("note") or "").strip(),
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "stage": lead.stage, "stage_label": lead.get_stage_display()})


@login_required
@require_POST
def app_lead_interest(request, lead_id):
    """Add or drop one product requirement on a lead."""
    lead = get_object_or_404(_lead_qs(request), pk=lead_id)
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

    if body.get("remove"):
        lead.interests.filter(pk=body.get("remove")).delete()
        return JsonResponse({"ok": True})

    product = Product.objects.selectable().main().filter(pk=body.get("product_id")).first()
    if not product:
        return JsonResponse({"ok": False, "error": "Unknown product."}, status=400)

    amount = None
    raw = body.get("amount")
    if raw not in (None, ""):
        try:
            amount = Decimal(str(raw))
        except InvalidOperation:
            return JsonResponse({"ok": False, "error": "Invalid amount."}, status=400)

    interest, _created = LeadInterest.objects.update_or_create(
        lead=lead, product=product,
        defaults={"amount": amount, "note": str(body.get("note") or "").strip()[:255]},
    )
    return JsonResponse({"ok": True, "id": interest.id})


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
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = {}
    action = body.get("action")

    if action == "discard":
        lead_service.mark_lost(lead, user=request.user, reason=str(body.get("reason") or "").strip())
    elif action == "undiscard":
        lead_service.reopen(lead, user=request.user)
    elif action == "convert":
        try:
            client = lead_service.convert_to_client(lead, user=request.user)
        except ValueError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        return JsonResponse({"ok": True, "client_id": client.id})
    else:
        return JsonResponse({"ok": False, "error": "Unknown action."}, status=400)
    return JsonResponse({"ok": True})


# ── Screen 10: Reports summary ───────────────────────────────────────────────

@login_required
@require_GET
def app_report_summary(request):
    """Period-grouped business trend (product-split) + product mix +
    (admins/managers) employee leaderboard. Query params: `period`
    (month|quarter|half|year, default month) and `columns` (default 6)."""
    emp = _emp(request)
    is_admin = _is_admin(request)
    is_manager = bool(emp and emp.role == "manager")
    firm_wide = permissions.can(request.user, "employee_performance")

    base = Sale.objects.filter(status=Sale.STATUS_APPROVED)
    if not firm_wide:
        if emp is None:
            return JsonResponse({"ok": False, "error": "No employee account."}, status=403)
        base = base.filter(employee=emp)

    data = business_overview_data(
        base,
        period=request.GET.get("period", "month"),
        columns=request.GET.get("columns", 6),
        with_leaderboard=firm_wide,
    )

    resp = {
        "firm_wide": firm_wide,
        "period": data["period"],
        "columns": data["columns"],
        "buckets": data["buckets"],
        "current_label": data["current_label"],
        "current_sublabel": data["current_sublabel"],
        "trend": [
            {
                "label": t["label"],
                "sublabel": t["sublabel"],
                "amount": _money(t["amount"]),
                "count": t["count"],
                "by_product": [_money(v) for v in t["by_product"]],
            }
            for t in data["trend"]
        ],
        "products": [
            {"name": p["name"], "amount": _money(p["amount"]), "count": p["count"]}
            for p in data["products"]
        ],
    }
    if firm_wide:
        resp["leaderboard"] = [
            {
                "name": e["name"],
                "amount": _money(e["amount"]),
                "points": _money(e["points"]),
                "by_product": [_money(v) for v in e["by_product"]],
            }
            for e in data["leaderboard"]
        ]
    return JsonResponse(resp)


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
    diagnostics = body.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    AppDeviceStatus.objects.update_or_create(
        user=request.user,
        defaults={
            "calls_granted": bool(body.get("calls_granted")),
            "overlay_granted": bool(body.get("overlay_granted")),
            "notifications_granted": bool(body.get("notifications_granted")),
            "app_version": str(body.get("app_version") or "")[:20],
            "diagnostics": diagnostics,
        },
    )
    return JsonResponse({"ok": True})


@login_required
@require_POST
def app_crash(request):
    """A crash from an employee's phone, posted on the next launch.

    Self-hosted APK, no Play Console: without this, a crash in the field
    produced exactly one signal — "the app closed". Stored as an AuditLog row
    so it lands in a place admins already read, with no new model.
    """
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False}, status=400)

    from ..models import AuditLog
    message = str(body.get("message") or "")[:500]
    AuditLog.objects.create(
        action="app.crash",
        actor=request.user,
        target_model="MobileApp",
        summary=f"App crash on {str(body.get('device') or 'unknown device')[:40]}: {message}"[:255],
        details={
            "message": message,
            "stack": str(body.get("stack") or "")[:6000],
            "app_version": str(body.get("app_version") or "")[:20],
            "device": str(body.get("device") or "")[:80],
            "android": body.get("android"),
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
        started_at__week_day__in=cfg.work_week_days_django(),
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
        # Outgoing-and-connected, matching the web call_analytics "Connected"
        # card — the app previously counted connected calls of BOTH directions,
        # which made web and app disagree for the same day.
        connected_calls=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING, connected=True)),
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
        started_at__week_day__in=cfg.work_week_days_django(),
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
            # Outgoing-only, matching the web per-employee "Dialed"/"Connected"
            # columns (previously counted every direction incl. missed).
            calls=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING)),
            connected_count=Count("id", filter=Q(direction=CallLogEntry.DIRECTION_OUTGOING, connected=True)),
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

    from ..services.calls import outcome_breakdown

    if rng == "today":
        out_start = out_end = today
    elif rng == "week":
        out_start, out_end = today - timedelta(days=6), today
    else:
        out_start, out_end = today.replace(day=1), today

    return JsonResponse({
        "totals": {
            "dialed": totals["dialed"] or 0,
            "connected": totals["connected_calls"] or 0,
            "received": totals["received"] or 0,
            "missed": totals["missed"] or 0,
            "talk_minutes": round((totals["talk_seconds"] or 0) / 60, 1),
        },
        # What those calls produced — same helper the web page uses.
        "outcomes": outcome_breakdown(
            out_start, out_end,
            employee_id=(int(request.GET["employee_id"])
                         if str(request.GET.get("employee_id", "")).isdigit() else None),
        ),
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
            # Multiyear health later years pay on the anniversary with no sale
            # row behind them, so they are added to both point totals.
            "total_points": _money((approved.aggregate(t=Sum("points"))["t"] or 0)
                                   + _incentives.accrued_points(e, date(2000, 1, 1), timezone.localdate())),
            "clients": Client.objects.filter(mapped_to=e).count(),
            "month_amount": _money(month.aggregate(t=Sum("amount"))["t"]),
            "month_points": _money((month.aggregate(t=Sum("points"))["t"] or 0)
                                   + _incentives.accrued_points(
                                       e, timezone.localdate().replace(day=1), timezone.localdate())),
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

    from ..forms import validate_pan
    from django.core.exceptions import ValidationError as _VErr
    try:
        pan = validate_pan(body.get("pan"), required=True)
    except _VErr as e:
        return JsonResponse({"ok": False, "error": e.messages[0]}, status=400)

    digits = digits10(phone)
    if len(digits) >= 10 and Client.objects.filter(phone__endswith=digits).exists():
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
        pan=pan,
        address=str(body.get("address") or "").strip() or None,
        date_of_birth=dob,
        mapped_to=mapped_to,
        status="Mapped" if mapped_to else "Unmapped",
    )
    # PAN is mandatory here — link any RTA folios/SIPs already imported for it.
    from ..services.rta_feed import relink_folios
    relink_folios()
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
        # slab_mode decides what a slab payout MEANS — rupees earned-to-date, or
        # a percent for the band. The app renders "pts" off slab_unit; without
        # it a 2.00% health band reads as "2 pts".
        rules.append({
            "id": r.id,
            "product": r.product_ref.name if r.product_ref_id else r.product,
            "unit_amount": _money(r.unit_amount),
            "points_per_unit": _money(r.points_per_unit),
            "slab_mode": r.slab_mode,
            "slab_unit": "percent" if r.slab_mode == IncentiveRule.MODE_RATE else "points",
            "slab_period": r.slab_period,
            "active": r.active,
            "slabs": [
                {"id": s.id, "threshold": _money(s.threshold), "payout": _money(s.payout), "label": s.label}
                for s in r.slabs.all()
            ],
        })
    available = [
        {"id": p.id, "name": p.name}
        for p in Product.objects.selectable().main().filter(
            domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
        ).in_display_order()
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
        for p in Product.objects.selectable().main().filter(
            domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
        ).in_display_order()
    ]
    return JsonResponse({"campaigns": campaigns, "products": products})


# ── Screen 13: Lead sheets — module removed ─────────────────────────────────
# The Lead Records (lead sheets) module was removed from the CRM. These
# endpoints remain as graceful stubs so app versions that still ship the
# Sheets screen see an empty list instead of an error. Remove once all
# devices are on an app version without the Sheets screen.


@login_required
@require_GET
def app_sheets(request):
    return JsonResponse({"results": []})


@login_required
@require_GET
def app_sheet_records(request, sheet_id):
    return JsonResponse({"ok": False, "error": "Lead Records has been removed."}, status=410)


@login_required
@require_POST
def app_sheet_record_save(request, sheet_id):
    return JsonResponse({"ok": False, "error": "Lead Records has been removed."}, status=410)


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
    if permissions.is_admin(request.user):
        return True, True  # allowed, firm_wide
    if emp and emp.role == "manager":
        return permissions.can(request.user, "employee_performance"), True
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

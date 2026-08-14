"""Sale workflow logic shared by the web views and the app JSON API.

Every state change a sale can go through lives here — creation finalization,
approve/reject review, delete — so the web pages and the mobile endpoints
can never drift apart. Views stay thin: they parse input and render output.

Points themselves are computed by ``Sale.compute_points()`` (invoked from
``Sale.save()``); what this module owns is the *workflow* around it,
including the sibling-recompute that keeps slab/campaign payouts consistent
after any status change.
"""
from datetime import timedelta

from django.utils import timezone

from ..models import Product, Sale

# How far either side of the entered date we look for the same sale again.
# Two months: long enough to catch "I forgot I booked this last month",
# short enough that a genuine yearly repeat on the same plan is never flagged.
DUPLICATE_WINDOW_DAYS = 60


def find_duplicate(sale, *, window_days=DUPLICATE_WINDOW_DAYS):
    """The same sale already on the books near this date, or None.

    Same client + same product + same amount is how a sale gets entered twice:
    someone forgets they already booked it. The **date is not part of the
    match** — the second entry is usually keyed on a different day — so the
    search is bounded to a window around the new sale's date instead.

    Rejected sales don't count: re-entering one after a correction is exactly
    what should happen. Callers decide what to do with the hit; nothing here
    blocks a save, because a genuine second identical sale does happen.
    """
    if not (sale.client_id and sale.product and sale.amount is not None):
        return None
    on = sale.date or timezone.localdate()
    qs = (
        Sale.objects.filter(
            client_id=sale.client_id,
            product=sale.product,
            amount=sale.amount,
            date__gte=on - timedelta(days=window_days),
            date__lte=on + timedelta(days=window_days),
        )
        .exclude(status=Sale.STATUS_REJECTED)
        .select_related("client", "employee__user")
    )
    if sale.pk:
        qs = qs.exclude(pk=sale.pk)
    return qs.order_by("-date", "-created_at").first()


def recompute_sibling_sales(sale):
    """Re-run points on sales that share this sale's slab pool (same employee +
    product within the rule's accumulation window, or the campaign window).
    Needed after a status change or delete so slab-delta payouts stay
    consistent — and, for rate slabs, so every sale in the window re-rates when
    the volume crosses a band."""
    from . import incentives as inc

    qs = Sale.objects.filter(employee=sale.employee).exclude(pk=sale.pk)
    rule = None
    if sale.campaign_id:
        qs = qs.filter(date__range=[sale.campaign.start_date, sale.campaign.end_date])
    elif sale.date:
        rule = sale._rule()
        if rule is not None:
            start, end = inc.period_bounds(rule, sale.date)
            qs = qs.filter(date__gte=start, date__lte=end)
        else:
            qs = qs.filter(date__year=sale.date.year, date__month=sale.date.month)
    if rule is not None:
        # The slab pool is everything the rule prices — a plan-level sale
        # shares its pool with the parent product's other sales.
        qs = qs.filter(inc.rule_sales_q(rule))
    elif sale.product_ref_id:
        qs = qs.filter(product_ref_id=sale.product_ref_id)
    else:
        qs = qs.filter(product=sale.product)
    for sibling in qs.order_by("date", "id"):
        sibling.save()  # save() recomputes points

    # And the sale itself. compute_points() excludes the row being saved from
    # its own period, which is right for the cumulative volume but understates
    # the month the derived legacy deduction is read off. One more pass, now
    # that it is in the table, settles it. Skipped after a delete — re-saving
    # would resurrect the row.
    if sale.pk and Sale.objects.filter(pk=sale.pk).exists():
        sale.refresh_from_db()
        sale.save()


def _own_book(qs, employee):
    """Scope to one person's book — sold by them, or theirs to service."""
    from django.db.models import Q

    if employee is None:
        return qs
    return qs.filter(Q(employee=employee) | Q(client__mapped_to=employee))


def renewal_due_sale_ids(today, employee=None, within_days=7):
    """Approved insurance policies renewing within `within_days`.

    The dashboard counts these and "My day" links to them, so both read this
    one selector — a count that doesn't match the list it opens is worse than
    no count at all.
    """
    from django.db.models import Q

    from ..models import Sale

    qs = _own_book(
        Sale.objects.filter(status=Sale.STATUS_APPROVED).filter(
            Q(product_ref__code__in=["HEALTH_INS", "LIFE_INS"])
            | Q(product__iexact="Health Insurance")
            | Q(product__iexact="Life Insurance")
        ).select_related("product_ref"),
        employee,
    )
    ids = []
    for sale in qs:
        nxt = sale.next_renewal_date(today)
        if nxt and 0 <= (nxt - today).days <= within_days:
            ids.append(sale.pk)
    return ids


def emi_due_sale_ids(today, employee=None):
    """Multiyear-EMI policies whose EMI schedule covers `today`'s month."""
    from ..management.commands.emi_reminders import emi_window_contains
    from ..models import Sale

    qs = _own_book(Sale.objects.filter(emi_months__gt=0, status=Sale.STATUS_APPROVED),
                   employee)
    return [s.pk for s in qs
            if s.date and emi_window_contains(s, today.year, today.month)]


def snapshot_ppt_margin(sale):
    """Lock in the FYC (sale margin %) for a PPT-priced life plan at the current
    designation. Called on create and edit so the sale carries a frozen margin —
    a later MDRT toggle change never rewrites it. No-op for non-PPT products."""
    from ..models import FirmSettings

    if sale.product_ref_id and sale.ppt:
        mdrt = FirmSettings.get_settings().is_mdrt_active()
        fyc = sale.product_ref.fyc_for_ppt(sale.ppt, mdrt=mdrt)
        if fyc is not None:
            sale.margin_percent_snapshot = fyc
            return
    # No PPT (or no matching rate) → clear any stale snapshot.
    sale.margin_percent_snapshot = None
    if not (sale.product_ref_id and sale.product_ref.has_ppt_rates):
        sale.ppt = ""


def finalize_new_sale(sale, actor, *, auto_approve):
    """Apply the shared creation rules to an unsaved Sale and persist it.

    - resolves product_ref from the product name when missing
    - admin-entered sales are approved on the spot; everything else starts
      pending review
    Points are computed inside save().

    A sale that lands approved immediately changes its period's pool, so the
    siblings are recomputed here too. Without it an admin-entered sale that
    crosses a rate band priced only itself at the new band and left the rest of
    the month on the old one — the employee-entry path got this right because
    approval routes through _finish_review.
    """
    if sale.product and not sale.product_ref_id:
        sale.product_ref = Product.objects.filter(name=sale.product).first()

    snapshot_ppt_margin(sale)

    if auto_approve:
        sale.status = Sale.STATUS_APPROVED
        sale.approved_by = actor
        sale.approved_at = timezone.now()
    else:
        sale.status = Sale.STATUS_PENDING
        sale.approved_by = None
        sale.approved_at = None
    sale.rejection_reason = ""
    sale._audit_actor = actor  # picked up by the AuditLog signal
    sale.save()
    if sale.status == Sale.STATUS_APPROVED:
        recompute_sibling_sales(sale)
    _sync_insurance_tracker(sale)
    return sale


def _sync_insurance_tracker(sale):
    """Mirror an insurance sale into the Insurance Tracker (or remove it if the
    sale is no longer approved). Best-effort — a tracker hiccup must never
    block booking a sale."""
    from . import insurance_sync
    try:
        if sale.status == Sale.STATUS_APPROVED:
            insurance_sync.sync_policy_from_sale(sale)
        else:
            insurance_sync.unsync_policy_for_sale(sale)
    except Exception:
        pass


def approve_sale(sale, actor):
    sale.status = Sale.STATUS_APPROVED
    sale.rejection_reason = ""
    _finish_review(sale, actor)


def reject_sale(sale, actor, reason=""):
    sale.status = Sale.STATUS_REJECTED
    sale.rejection_reason = (reason or "").strip()
    _finish_review(sale, actor)


def _finish_review(sale, actor):
    sale.approved_by = actor
    sale.approved_at = timezone.now()
    sale._audit_actor = actor  # picked up by the AuditLog signal
    sale.save()
    recompute_sibling_sales(sale)
    _sync_insurance_tracker(sale)


def delete_sale(sale, actor):
    sale._audit_actor = actor  # picked up by the AuditLog signal
    sale.delete()
    recompute_sibling_sales(sale)

"""Sale workflow logic shared by the web views and the app JSON API.

Every state change a sale can go through lives here — creation finalization,
approve/reject review, delete — so the web pages and the mobile endpoints
can never drift apart. Views stay thin: they parse input and render output.

Points themselves are computed by ``Sale.compute_points()`` (invoked from
``Sale.save()``); what this module owns is the *workflow* around it,
including the sibling-recompute that keeps slab/campaign payouts consistent
after any status change.
"""
from django.utils import timezone

from ..models import Product, Sale


def recompute_sibling_sales(sale):
    """Re-run points on sales that share this sale's slab pool (same employee +
    product within the slab month, or the campaign window). Needed after a
    status change or delete so slab-delta payouts stay consistent."""
    qs = Sale.objects.filter(employee=sale.employee).exclude(pk=sale.pk)
    if sale.campaign_id:
        qs = qs.filter(date__range=[sale.campaign.start_date, sale.campaign.end_date])
    elif sale.date:
        qs = qs.filter(date__year=sale.date.year, date__month=sale.date.month)
    if sale.product_ref_id:
        qs = qs.filter(product_ref_id=sale.product_ref_id)
    else:
        qs = qs.filter(product=sale.product)
    for sibling in qs.order_by("date", "id"):
        sibling.save()  # save() recomputes points


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

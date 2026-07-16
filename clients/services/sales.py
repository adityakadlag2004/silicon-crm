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


def finalize_new_sale(sale, actor, *, auto_approve):
    """Apply the shared creation rules to an unsaved Sale and persist it.

    - resolves product_ref from the product name when missing
    - admin-entered sales are approved on the spot; everything else starts
      pending review
    Points are computed inside save().
    """
    if sale.product and not sale.product_ref_id:
        sale.product_ref = Product.objects.filter(name=sale.product).first()

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
    return sale


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


def delete_sale(sale, actor):
    sale._audit_actor = actor  # picked up by the AuditLog signal
    sale.delete()
    recompute_sibling_sales(sale)

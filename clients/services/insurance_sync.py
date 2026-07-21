"""Keep the Insurance Tracker in step with sales and renewals.

Two entry points into the tracker feed it automatically:

  * a Health/Life insurance **sale** creates one policy (idempotent per sale,
    keyed on ``InsurancePolicy.source_sale``);
  * a **renewal** logged for a client who has *no* policy of that type on the
    tracker back-fills one, so the old book sold before the tracker existed
    gets captured the first time it renews.

Every date here is measured from the policy's own commencement date — the
sale's ``policy_date`` or the renewal's ``renewal_date`` — never the sale
(approval) date.
"""
from __future__ import annotations

from datetime import timedelta

from ..models import InsurancePolicy, Renewal, Sale

# Product identity → tracker insurance_type.
_SALE_TYPE = {
    "HEALTH_INS": InsurancePolicy.TYPE_HEALTH,
    "LIFE_INS": InsurancePolicy.TYPE_LIFE,
    "health insurance": InsurancePolicy.TYPE_HEALTH,
    "life insurance": InsurancePolicy.TYPE_LIFE,
}
_RENEWAL_TYPE = {
    Renewal.PRODUCT_TYPE_HEALTH: InsurancePolicy.TYPE_HEALTH,
    Renewal.PRODUCT_TYPE_LIFE: InsurancePolicy.TYPE_LIFE,
}


def _sale_insurance_type(sale: Sale):
    """TYPE_HEALTH / TYPE_LIFE for an insurance sale, else None."""
    if sale.product_ref_id and sale.product_ref.code in _SALE_TYPE:
        return _SALE_TYPE[sale.product_ref.code]
    return _SALE_TYPE.get((sale.product or "").strip().lower())


def _plus_one_year(d):
    """Same date next year; 29 Feb → 28 Feb (policy stays continuous)."""
    if not d:
        return None
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def sync_policy_from_sale(sale: Sale) -> InsurancePolicy | None:
    """Create or update the tracker policy for a Health/Life insurance sale.

    Idempotent: one policy per sale, linked via ``source_sale``. Only approved
    insurance sales are tracked — a pending or rejected sale is not a policy
    yet. Returns the policy, or None when the sale isn't trackable.
    """
    kind = _sale_insurance_type(sale)
    if kind is None or sale.status != Sale.STATUS_APPROVED:
        return None

    start = sale.renewal_basis          # policy_date, or sale date for legacy rows
    policy, _created = InsurancePolicy.objects.get_or_create(
        source_sale=sale,
        defaults={
            "client": sale.client,
            "policy_number": f"SALE-{sale.pk}",   # placeholder until the real number is entered
            "insurer": "",
            "insurance_type": kind,
            "status": InsurancePolicy.STATUS_ACTIVE,
        },
    )
    # Refresh the derived fields on every sync so an edited sale stays in step,
    # but never clobber a policy_number/insurer an admin has since filled in.
    policy.client = sale.client
    policy.insurance_type = kind
    policy.premium_amount = sale.amount or 0
    policy.sum_insured = sale.cover_amount or 0
    policy.start_date = start
    policy.end_date = _plus_one_year(start)
    policy.relationship_manager = sale.employee
    policy.save()
    return policy


def unsync_policy_for_sale(sale: Sale) -> None:
    """Drop the auto-created policy when a sale is un-approved or deleted.

    Only removes a still-placeholder policy (no real number/insurer entered);
    once someone has curated it, we keep it and just detach the link.
    """
    policy = InsurancePolicy.objects.filter(source_sale=sale).first()
    if not policy:
        return
    untouched = (not policy.insurer) and policy.policy_number == f"SALE-{sale.pk}"
    if untouched:
        policy.delete()
    else:
        policy.source_sale = None
        policy.save(update_fields=["source_sale"])


def client_health_life_policies(client):
    """Active-ish Health/Life policies for a client, newest first — the list
    the add-renewal screen shows once a client is picked."""
    return (
        InsurancePolicy.objects
        .filter(client=client, insurance_type__in=[InsurancePolicy.TYPE_HEALTH,
                                                   InsurancePolicy.TYPE_LIFE])
        .order_by("-start_date", "-id")
    )


def sync_policy_from_renewal(renewal: Renewal) -> InsurancePolicy | None:
    """Back-fill a tracker policy from a renewal when the client has none.

    A renewal is logged against an existing policy; if the tracker doesn't know
    that policy yet (old book, sold before the tracker), create it so the
    client's insurance shows up. If a policy of that type already exists we
    leave it alone — the renewal is against it, not a new one.
    """
    kind_name = renewal.insurance_kind          # robust to legacy rows
    kind = {"health": InsurancePolicy.TYPE_HEALTH,
            "life": InsurancePolicy.TYPE_LIFE}.get(kind_name)
    if kind is None:
        return None
    existing = InsurancePolicy.objects.filter(
        client=renewal.client, insurance_type=kind).first()
    if existing:
        return existing

    start = renewal.renewal_date
    return InsurancePolicy.objects.create(
        client=renewal.client,
        policy_number=f"RENEWAL-{renewal.pk}",
        insurer="",
        plan_name=(renewal.product_name or "").strip(),
        insurance_type=kind,
        status=InsurancePolicy.STATUS_ACTIVE,
        premium_amount=renewal.premium_amount or 0,
        start_date=start,
        end_date=renewal.renewal_end_date or _plus_one_year(start),
        relationship_manager=renewal.employee,
        notes="Auto-created from a renewal entry — confirm the policy details.",
    )

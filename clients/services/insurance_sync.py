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
    number = (sale.policy_number or "").strip() or f"SALE-{sale.pk}"
    policy, _created = InsurancePolicy.objects.get_or_create(
        source_sale=sale,
        defaults={
            "client": sale.client,
            "policy_number": number,
            "insurer": "",
            "insurance_type": kind,
            "status": InsurancePolicy.STATUS_ACTIVE,
        },
    )
    # Refresh the derived fields on every sync so an edited sale stays in step.
    # The policy number comes from the sale now that it's mandatory, so keep it
    # current; a blank sale number never overwrites an existing real one.
    policy.client = sale.client
    policy.insurance_type = kind
    if (sale.policy_number or "").strip():
        policy.policy_number = number
    policy.premium_amount = sale.amount or 0
    policy.sum_insured = sale.cover_amount or 0
    policy.start_date = start
    # A multiyear policy is paid through its whole term — expire the tracker
    # entry when Sale.coverage_end() says, or a 3-year policy reads as due in
    # 12 months and the renewal reminder fires two years early.
    policy.end_date = sale.coverage_end() or _plus_one_year(start)
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
    untouched = (not policy.insurer) and policy.policy_number in (
        f"SALE-{sale.pk}", (sale.policy_number or "").strip())
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


# One renewal per policy per cycle. A yearly policy collected twice inside ~10
# months is a double entry, not two renewals; the shorter frequencies scale the
# same way. Deliberately loose — a genuine early renewal is rarer than a staff
# member entering the same receipt twice.
_CYCLE_DAYS = {
    Renewal.FREQUENCY_YEARLY: 300,
    Renewal.FREQUENCY_HALF_YEARLY: 150,
    Renewal.FREQUENCY_QUARTERLY: 75,
    Renewal.FREQUENCY_MONTHLY: 25,
}


def find_policy_by_number(number, client=None):
    """The tracker policy carrying `number` (client-scoped when given).

    Normalises the way ``InsurancePolicy.save()`` does, so a typed "ins123 "
    finds the stored "INS123" instead of creating a second policy for it.
    """
    number = (number or "").strip().upper()
    if not number:
        return None
    qs = InsurancePolicy.objects.filter(policy_number=number)
    if client is not None:
        qs = qs.filter(client=client)
    return qs.select_related("client").first()


def duplicate_renewal(*, client, renewal_date, frequency, policy=None,
                      policy_number="", exclude_pk=None):
    """The renewal this one would duplicate, or None.

    Checked BEFORE the row is written, by both the web view and the app API —
    the two never share a save path, so a guard on either one alone leaves the
    other door open.

    A duplicate is the same *policy* collected again inside one renewal cycle.
    When no policy is picked, a typed number that already exists on the tracker
    identifies the policy just as well — that is the case worth catching, since
    it is exactly how the same policy gets entered twice under two numbers.
    """
    policy = policy or find_policy_by_number(policy_number, client)
    if policy is None or not renewal_date:
        return None
    window = timedelta(days=_CYCLE_DAYS.get(frequency, 300))
    qs = Renewal.objects.filter(
        policy=policy,
        renewal_date__gt=renewal_date - window,
        renewal_date__lt=renewal_date + window,
    ).select_related("employee__user")
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.order_by("-renewal_date").first()


def duplicate_message(existing):
    """Plain-language warning naming the renewal already on file."""
    who = ""
    if existing.employee_id and existing.employee.user_id:
        who = f" by {existing.employee.user.get_full_name() or existing.employee.user.username}"
    number = existing.policy.policy_number if existing.policy_id else "this policy"
    return (
        f"Already added: policy {number} was renewed on "
        f"{existing.renewal_date:%d %b %Y} (₹{existing.premium_amount:,.0f} collected "
        f"{existing.premium_collected_on:%d %b %Y}{who}). "
        f"Check the renewals list before adding it again."
    )


def _advance_cover(policy, renewal):
    """Move a policy's expiry to the period the collected renewal has paid for.

    Without this the tracker's ``end_date`` stays at whatever the policy was
    first created with, so "Expiring ≤30d" goes stale after the first renewal
    and ``renewal_reminders`` keeps chasing a premium already in the bank.
    Only ever moves the date *forward* — back-entering an old renewal must not
    pull live cover backwards.
    """
    new_end = renewal.renewal_end_date or _plus_one_year(renewal.renewal_date)
    if not new_end or (policy.end_date and new_end <= policy.end_date):
        return
    policy.end_date = new_end
    fields = ["end_date"]
    # A lapsed policy whose premium was just collected is in force again.
    # Cancelled/matured are deliberate end-states and stay put.
    if policy.status == InsurancePolicy.STATUS_LAPSED:
        policy.status = InsurancePolicy.STATUS_ACTIVE
        fields.append("status")
    policy.save(update_fields=fields)


def link_renewal_to_policy(renewal, *, selected_policy_id=None, new_policy_number=""):
    """Attach a renewal to a policy on the tracker.

    Priority:
      1. an existing policy the user ticked (``selected_policy_id``) — the
         renewal is logged against it;
      2. otherwise, for a Health/Life renewal, create a policy — using the
         ``new_policy_number`` the user typed when the client had none, else a
         placeholder — and link to it;
      3. non-insurance renewals link to nothing.

    Sets and saves ``renewal.policy``. Returns the policy, or None.
    """
    kind_name = renewal.insurance_kind
    kind = {"health": InsurancePolicy.TYPE_HEALTH,
            "life": InsurancePolicy.TYPE_LIFE}.get(kind_name)
    if kind is None:
        return None          # "Other" renewals (FD etc.) carry no policy

    if selected_policy_id:
        # The ticked policy must match this renewal's product line AND belong
        # to the client — otherwise a Health policy could silently absorb a
        # Life renewal (the pre-selection bug). On any mismatch, ignore the
        # selection and fall through to creating the right-type policy.
        policy = InsurancePolicy.objects.filter(
            pk=selected_policy_id, client=renewal.client,
            insurance_type=kind).first()
        if policy:
            renewal.policy = policy
            renewal.save(update_fields=["policy"])
            _advance_cover(policy, renewal)
            return policy

    number = (new_policy_number or "").strip()
    policy = InsurancePolicy.objects.create(
        client=renewal.client,
        policy_number=number or f"RENEWAL-{renewal.pk}",
        insurer="",
        plan_name=(renewal.product_name or "").strip(),
        insurance_type=kind,
        status=InsurancePolicy.STATUS_ACTIVE,
        premium_amount=renewal.premium_amount or 0,
        start_date=renewal.renewal_date,
        end_date=renewal.renewal_end_date or _plus_one_year(renewal.renewal_date),
        relationship_manager=renewal.employee,
        notes="Auto-created from a renewal entry — confirm the policy details.",
    )
    renewal.policy = policy
    renewal.save(update_fields=["policy"])
    return policy

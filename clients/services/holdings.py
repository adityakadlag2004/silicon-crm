"""What a client holds, read off the sales book and the renewals against it.

The client profile used to show six hard-coded cards fed by denormalised
columns on Client, so it could say "Health: Yes, cover 5,00,000" but never
*which* policy, how many, or what the number was — the first thing anyone
opening a client asks.

This groups the client's approved sales by **main product** (a sub-product's
business folds into its parent, the same roll-up the rest of the system uses)
and hands back the individual records behind each line, so the profile can
print one row per policy.

Renewals ride along on the insurance lines: a renewal is a premium collected
against a policy, not a new holding, so it adds to `collected` and appears in
the rows — it never inflates the cover.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Q

from ..models import InsurancePolicy, Product, Renewal, Sale

# Lines whose headline figure is cover, not the amount paid.
_COVER_LINES = {"HEALTH_INS", "LIFE_INS", "MOTOR_INS"}


def _main_product(sale):
    """The catalog line a sale belongs to — its product, or that product's
    parent when a sub-product names the exact plan sold."""
    ref = sale.product_ref
    if ref is None:
        return None, (sale.product or "Other").strip()
    if ref.parent_id:
        return ref.parent, ref.parent.name
    return ref, ref.name


def portfolio(client):
    """Ordered list of the product lines this client holds.

    Each entry:
      ``code``       catalog code of the main product (or "" for legacy rows)
      ``label``      product name
      ``count``      how many records make up the line
      ``amount``     total paid (premium / investment)
      ``cover``      total sum assured, insurance lines only
      ``collected``  renewal premium collected against the line
      ``is_cover``   whether the headline figure is cover
      ``rows``       the individual records, newest first
    """
    sales = (
        Sale.objects.filter(client=client, status=Sale.STATUS_APPROVED)
        .select_related("product_ref", "product_ref__parent", "employee__user")
        .order_by("-date", "-id")
    )

    lines: dict[str, dict] = {}

    def bucket(code, label, order):
        key = code or label
        if key not in lines:
            lines[key] = {
                "code": code, "label": label, "order": order,
                "count": 0, "amount": Decimal("0"), "cover": Decimal("0"),
                "collected": Decimal("0"),
                "is_cover": code in _COVER_LINES, "rows": [],
            }
        return lines[key]

    for sale in sales:
        product, label = _main_product(sale)
        code = product.code if product else ""
        entry = bucket(code, label, product.display_order if product else 999)
        entry["count"] += 1
        entry["amount"] += sale.amount or 0
        entry["cover"] += sale.cover_amount or 0
        entry["rows"].append({
            "kind": "sale",
            "date": sale.policy_date or sale.date,
            "policy_number": (sale.policy_number or "").strip(),
            "plan": sale.product_ref.name if sale.product_ref_id else sale.product,
            "amount": sale.amount or 0,
            "cover": sale.cover_amount or 0,
            "years": sale.policy_years or 1,
            "by": sale.employee.user.get_full_name() or sale.employee.user.username
                  if sale.employee_id and sale.employee.user_id else "",
        })

    # Renewals: premium collected against a line the client already holds. A
    # renewal for a line with no sale still opens its own line — that is the
    # old book, sold before this system existed, and hiding it would leave the
    # profile blank for exactly those clients.
    renewals = (
        Renewal.objects.filter(client=client)
        .select_related("product_ref", "product_ref__parent", "policy", "employee__user")
        .order_by("-renewal_date", "-id")
    )
    for r in renewals:
        ref = r.product_ref
        product = (ref.parent if (ref and ref.parent_id) else ref)
        code = product.code if product else ""
        label = product.name if product else (r.product_name or r.get_product_type_display())
        entry = bucket(code, label, product.display_order if product else 999)
        entry["count"] += 1
        entry["collected"] += r.premium_amount or 0
        entry["rows"].append({
            "kind": "renewal",
            "date": r.renewal_date,
            "policy_number": (r.policy.policy_number if r.policy_id else ""),
            "plan": r.product_name or label,
            "amount": r.premium_amount or 0,
            "cover": Decimal("0"),
            "years": 1,
            "collected_on": r.premium_collected_on,
            "filed": r.policy_doc_submitted,
            "by": r.employee.user.get_full_name() or r.employee.user.username
                  if r.employee_id and r.employee.user_id else "",
        })

    for entry in lines.values():
        entry["rows"].sort(key=lambda x: (x["date"] is None, x["date"]), reverse=True)

    return sorted(lines.values(), key=lambda e: (e["order"], e["label"]))


def policy_numbers(client):
    """Every policy number known for this client, tracker first.

    The tracker is the register, but a sale can carry a number before its
    policy row exists, so both are read and de-duplicated on the normalised
    (upper-cased, stripped) number the models already store.
    """
    numbers = {}
    for p in InsurancePolicy.objects.filter(client=client).order_by("insurance_type", "-end_date"):
        numbers.setdefault(p.policy_number, {
            "number": p.policy_number, "kind": p.get_insurance_type_display(),
            "policy": p, "end_date": p.end_date,
        })
    for s in (Sale.objects.filter(client=client, status=Sale.STATUS_APPROVED)
              .exclude(policy_number="").select_related("product_ref")):
        num = (s.policy_number or "").strip().upper()
        if num:
            numbers.setdefault(num, {
                "number": num,
                "kind": s.product_ref.name if s.product_ref_id else s.product,
                "policy": None, "end_date": None,
            })
    return list(numbers.values())

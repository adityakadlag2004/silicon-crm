"""Incentive maths — the one implementation of "what does this sale pay?".

``Sale.compute_points()`` calls this on every save, and the Incentive Structure
page's what-if calculator calls the same ``quote()`` with hand-typed numbers, so
the page can never drift from what actually gets paid.

Two slab shapes, chosen per rule (``IncentiveRule.slab_mode``):

* **bonus** — the slab payout is a rupee figure, and it is the total *earned to
  date* once the period's cumulative volume crosses the threshold. Only the
  difference against what the period already paid out is released, so the
  ladder never double-pays. Life insurance: a flat base rate on every policy
  plus this ladder over the financial year.
* **rate** — the slab payout is a *percent*, resolved from the period's
  cumulative volume and applied to this sale's amount. Health insurance: the
  seller's own monthly Fresh volume picks the band, mirroring the shape of the
  firm's own margin grid.

The accumulation window is ``IncentiveRule.slab_period`` — the calendar month
(health) or the Apr–Mar financial year (life).
"""

from datetime import date
from decimal import Decimal

FY_START_MONTH = 4

ZERO = Decimal("0")


def fy_start_year(d):
    """The financial year (Apr–Mar) a date belongs to, named by its start year."""
    return d.year if d.month >= FY_START_MONTH else d.year - 1


def period_bounds(rule, on_date):
    """(first_day, last_day) of the accumulation window `on_date` falls in."""
    from ..models import IncentiveRule

    if rule.slab_period == IncentiveRule.PERIOD_FY:
        y = fy_start_year(on_date)
        return date(y, FY_START_MONTH, 1), date(y + 1, FY_START_MONTH, 1) - _one_day()
    if on_date.month == 12:
        nxt = date(on_date.year + 1, 1, 1)
    else:
        nxt = date(on_date.year, on_date.month + 1, 1)
    return date(on_date.year, on_date.month, 1), nxt - _one_day()


def _one_day():
    from datetime import timedelta

    return timedelta(days=1)


def unit_rate_percent(rule):
    """The rule's flat base rate as a percent of the sale amount."""
    if not rule or not rule.unit_amount:
        return ZERO
    return (rule.points_per_unit or ZERO) / rule.unit_amount * Decimal("100")


def bonus_released_for(rule, volume):
    """Bonus a period would already have released by the time it reached
    `volume` — the payout of the highest rung that volume clears."""
    from ..models import IncentiveRule

    if rule is None or rule.slab_mode != IncentiveRule.MODE_BONUS:
        return ZERO
    volume = Decimal(str(volume or 0))
    band = next((s for s in rule.slabs.all().order_by("-threshold") if volume >= s.threshold), None)
    return band.payout if band else ZERO


def ladder(rule):
    """The rule's rungs low→high, each with the effective % it lands at.

    For a bonus ladder that is (base + rung) / threshold — what a seller who
    stops exactly on the rung takes home. For rate bands it is just the band.
    """
    from ..models import IncentiveRule

    if rule is None:
        return []
    base = unit_rate_percent(rule)
    rows = []
    for s in sorted(rule.slabs.all(), key=lambda x: x.threshold):
        if rule.slab_mode == IncentiveRule.MODE_RATE:
            rows.append({"slab": s, "effective": s.payout, "at_threshold": None})
        else:
            at = s.threshold * base / Decimal("100") + s.payout
            eff = (at / s.threshold * Decimal("100")) if s.threshold else ZERO
            rows.append({"slab": s, "effective": eff, "at_threshold": at})
    return rows


def quote(rule, amount, *, prior_volume=ZERO, prior_bonus=ZERO,
          policy_type="", is_health=False):
    """What one sale of `amount` pays under `rule`.

    `amount` is the *creditable* amount — the caller has already sliced a
    multiyear premium down to one year. `prior_volume` is the employee's
    approved creditable volume in this rule's period excluding this sale, and
    `prior_bonus` the bonus rupees the period has already released.

    Returns a dict with `base`, `bonus`, `total`, the `rate` percent that
    produced the base, the cumulative `volume` the slab was resolved against,
    and a human `basis` string the structure page prints as the explanation.
    """
    amount = Decimal(str(amount or 0))
    prior_volume = Decimal(str(prior_volume or 0))
    prior_bonus = Decimal(str(prior_bonus or 0))
    blank = {"base": ZERO, "bonus": ZERO, "total": ZERO, "rate": ZERO,
             "volume": amount, "band": None, "basis": "No incentive rule for this product."}
    if rule is None or not rule.active:
        return blank

    from ..models import IncentiveRule

    # Port is worked the same as Fresh but earns the firm a flat 15%, so it
    # pays its own reduced rate outside the volume ladder.
    if is_health and policy_type == "port":
        rate = rule.port_percent if rule.port_percent is not None else ZERO
        base = amount * rate / Decimal("100")
        return {"base": base, "bonus": ZERO, "total": base, "rate": rate,
                "volume": amount, "band": None,
                "basis": f"Port policy — flat {_pct(rate)}% of premium."}

    slabs = list(rule.slabs.all().order_by("-threshold"))
    volume = prior_volume + amount

    if slabs and rule.slab_mode == IncentiveRule.MODE_RATE:
        band = next((s for s in slabs if volume >= s.threshold), None)
        rate = band.payout if band else unit_rate_percent(rule)
        base = amount * rate / Decimal("100")
        window = "this month" if rule.slab_period == IncentiveRule.PERIOD_MONTH else "this financial year"
        return {"base": base, "bonus": ZERO, "total": base, "rate": rate,
                "volume": volume, "band": band,
                "basis": (f"₹{_n(volume)} sold {window} counting this sale — "
                          f"the {_pct(rate)}% band.")}

    rate = unit_rate_percent(rule)
    base = (amount / rule.unit_amount * rule.points_per_unit) if rule.unit_amount else ZERO
    bonus = ZERO
    band = None
    if slabs:
        band = next((s for s in slabs if volume >= s.threshold), None)
        payout = band.payout if band else ZERO
        bonus = max(payout - prior_bonus, ZERO)
    window = "this month" if rule.slab_period == IncentiveRule.PERIOD_MONTH else "this financial year"
    basis = f"Base {_pct(rate)}% of ₹{_n(amount)}."
    if slabs:
        if bonus > 0:
            basis += (f" Cumulative ₹{_n(volume)} {window} reaches the "
                      f"₹{_n(band.threshold)} rung (₹{_n(band.payout)} earned to date), "
                      f"₹{_n(prior_bonus)} already released → ₹{_n(bonus)} more.")
        elif band is not None:
            basis += (f" Cumulative ₹{_n(volume)} {window} is at the ₹{_n(band.threshold)} "
                      f"rung; its ₹{_n(band.payout)} was already released.")
        else:
            nxt = slabs[-1]
            basis += (f" Cumulative ₹{_n(volume)} {window} — ₹{_n(nxt.threshold - volume)} "
                      f"more reaches the first rung (₹{_n(nxt.payout)}).")
    return {"base": base, "bonus": bonus, "total": base + bonus, "rate": rate,
            "volume": volume, "band": band, "basis": basis}


def _n(v):
    return f"{Decimal(str(v or 0)):,.0f}"


def _pct(v):
    v = Decimal(str(v or 0))
    return f"{v.normalize():f}" if v == v.to_integral_value() else f"{v:.2f}".rstrip("0").rstrip(".")

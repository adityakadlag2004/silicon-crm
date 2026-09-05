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


def rule_sales_q(rule):
    """Q matching the sales `rule` prices: its product, any **sub-product** of
    it (a life sale carries the specific plan, the rule sits on the parent
    Life Insurance product), or legacy rows that predate product_ref (name
    match). Without the parent walk a plan-level sale silently drops out of
    the volume that sets the band and the FY ladder."""
    from django.db.models import Q

    if rule.product_ref_id:
        return (Q(product_ref_id=rule.product_ref_id)
                | Q(product_ref__parent_id=rule.product_ref_id)
                | (Q(product_ref__isnull=True) & Q(product=rule.product)))
    return Q(product=rule.product)


def life_bonus_status(rule, employee, fy_year):
    """One employee's whole financial year on a bonus ladder.

    Returns the FY totals plus a month-by-month strip. The prize is a pure
    function of the year's volume: how much life business the year holds picks
    the rung, and that rung is what is paid. A month that happens to be large
    changes nothing — there is no monthly deduction anywhere in this.
    """
    from calendar import monthrange
    from django.db.models import Sum

    from ..models import BonusPayout, Sale

    start = date(fy_year, FY_START_MONTH, 1)
    end = date(fy_year + 1, FY_START_MONTH, 1) - _one_day()

    qs = Sale.objects.filter(employee=employee, status=Sale.STATUS_APPROVED,
                             date__gte=start, date__lte=end).filter(rule_sales_q(rule))

    paid_by_month = {
        p.for_month: p.amount
        for p in BonusPayout.objects.filter(employee=employee, rule=rule,
                                            for_month__gte=start, for_month__lte=end)
    }

    months = []
    volume = ZERO
    base_total = ZERO
    released_total = ZERO
    paid_total = ZERO
    for i in range(12):
        m = (FY_START_MONTH - 1 + i) % 12 + 1
        y = fy_year + (1 if m < FY_START_MONTH else 0)
        m_start = date(y, m, 1)
        m_end = date(y, m, monthrange(y, m)[1])
        agg = qs.filter(date__gte=m_start, date__lte=m_end).aggregate(
            vol=Sum("amount"), pts=Sum("points"), bonus=Sum("bonus_points"))
        vol = agg["vol"] or ZERO
        bonus = agg["bonus"] or ZERO
        base = (agg["pts"] or ZERO) - bonus
        recorded = paid_by_month.get(m_start)
        volume += vol
        base_total += base
        released_total += bonus
        if recorded is not None:
            paid_total += recorded
        months.append({"year": y, "month": m, "volume": vol, "base": base,
                       "released": bonus, "running": volume,
                       "for_month": m_start, "paid": recorded})

    level = bonus_released_for(rule, volume)
    released = released_total + paid_total
    # The prize is settled after the year closes, never month by month: a rung
    # reached in June is a standing, not a bill. It falls due on 1 April, when
    # the year's final volume is known and cannot climb any further.
    payable_from = date(fy_year + 1, FY_START_MONTH, 1)
    fy_closed = _today() >= payable_from
    shortfall = max(level - released, ZERO)
    return {
        "rule": rule,
        "employee": employee, "fy_year": fy_year,
        "volume": volume, "base": base_total,
        "level": level, "released": released,
        "from_sales": released_total, "paid_manually": paid_total,
        "shortfall": shortfall,
        "payable_from": payable_from, "fy_closed": fy_closed,
        "due_now": shortfall if fy_closed else ZERO,
        "next": next_rung(rule, volume),
        "months": months,
    }


def policy_cancelled(sale):
    """True when the sale's tracker policy has been cancelled.

    A multiyear premium is paid up front, but the insurer can still cancel or
    the client can walk away — and then years 2..N were never earned. The
    tracker policy is where that is recorded, so it is read here rather than
    stored a second time: cancel the policy and the later years stop being
    scheduled at all. Nothing needs deleting, because an accrual row is only
    written on the day its year falls due.
    """
    from django.core.exceptions import ObjectDoesNotExist

    from ..models import InsurancePolicy

    try:
        return sale.policy.status == InsurancePolicy.STATUS_CANCELLED
    except ObjectDoesNotExist:
        return False


def accrual_schedule(sale):
    """[(year_index, due_date, slice)] for a multiyear health policy's later years.

    Year 1 was paid at sale time; years 2..N fall due on the policy's
    anniversary in each of those years. Empty for anything that isn't an
    approved multiyear health sale.
    """
    from ..models import Sale
    from ..models.sales import _add_years

    years = sale.policy_years or 1
    if years < 2 or sale.status != Sale.STATUS_APPROVED or not sale._is_health_product():
        return []
    if policy_cancelled(sale):
        return []
    basis = sale.renewal_basis
    if not basis:
        return []
    slice_amount = sale.annual_premium
    return [(n, _add_years(basis, n - 1), slice_amount) for n in range(2, years + 1)]


def issue_due_accruals(sale, *, on_date=None, dry_run=False):
    """Create the accrual rows a multiyear policy has reached the date for.

    Priced through the same ``quote()`` as a sale of that slice would be, using
    the employee's health volume in the month it falls due — so a later year
    lands in the band their business that month actually earns.

    ponytail: the accrual does not re-rate the month's other sales the way a
    real sale does. Later years are a trickle, and re-rating settled months
    would rewrite already-reported figures; revisit if volumes make it matter.
    """
    from ..models import IncentiveAccrual

    on_date = on_date or _today()
    made = []
    for year_index, due, amount in accrual_schedule(sale):
        if due > on_date:
            continue
        if IncentiveAccrual.objects.filter(sale=sale, year_index=year_index).exists():
            continue
        rule = sale._rule()
        prior, _bonus, _pts = period_totals(rule, sale.employee, due, is_health=True) if rule else (ZERO, ZERO, ZERO)
        q = quote(rule, amount, prior_volume=prior,
                  policy_type=sale.policy_type or "fresh", is_health=True)
        if dry_run:
            made.append(IncentiveAccrual(
                sale=sale, employee=sale.employee, year_index=year_index,
                due_date=due, amount=amount, points=q["total"]))
            continue
        made.append(IncentiveAccrual.objects.create(
            sale=sale, employee=sale.employee, year_index=year_index,
            due_date=due, amount=amount, points=q["total"]))
    return made


def accrued_points(employee, start, end):
    """Points issued from multiyear later years in [start, end]."""
    from django.db.models import Sum

    from ..models import IncentiveAccrual

    qs = IncentiveAccrual.objects.filter(due_date__gte=start, due_date__lte=end)
    if employee is not None:
        qs = qs.filter(employee=employee)
    return qs.aggregate(t=Sum("points"))["t"] or ZERO


def pending_accruals(employee=None):
    """Later-year points not yet due, soonest first — what's already banked
    for the future without another sale.

    ``employee=None`` reads the whole firm, which is how an admin sees the
    liability the book is carrying. Each row names the policy it hangs off, so
    a cancellation has something to cancel.
    """
    from ..models import Sale

    today = _today()
    rows = []
    sales = (Sale.objects.filter(status=Sale.STATUS_APPROVED, policy_years__gt=1)
             .select_related("client", "product_ref", "employee__user", "policy")
             .prefetch_related("accruals"))
    if employee is not None:
        sales = sales.filter(employee=employee)
    for sale in sales:
        if not sale._is_health_product():
            continue
        done = {a.year_index for a in sale.accruals.all()}
        rule = sale._rule()
        for year_index, due, amount in accrual_schedule(sale):
            if due <= today or year_index in done:
                continue
            q = quote(rule, amount, policy_type=sale.policy_type or "fresh", is_health=True)
            rows.append({"sale": sale, "employee": sale.employee,
                         "client": sale.client,
                         "policy": getattr(sale, "policy", None),
                         "policy_number": sale.policy_number,
                         "year_index": year_index, "due": due,
                         "amount": amount, "estimate": q["total"]})
    return sorted(rows, key=lambda r: (r["due"], r["sale"].id))


def future_points(employee=None):
    """Pending later-year points as a statement: total, then FY, then month.

    The shape an adviser's own commission statement uses — one line per
    financial year that opens into the months inside it — because the two
    questions are "how much is still coming" and "which month does it land in".
    """
    years = {}
    total = ZERO
    for row in pending_accruals(employee):
        total += row["estimate"]
        fy = fy_start_year(row["due"])
        year = years.setdefault(fy, {"fy_year": fy, "label": f"FY {fy % 100}-{(fy + 1) % 100:02d}",
                                     "points": ZERO, "amount": ZERO, "months": {}})
        year["points"] += row["estimate"]
        year["amount"] += row["amount"]
        key = (row["due"].year, row["due"].month)
        month = year["months"].setdefault(key, {"date": row["due"].replace(day=1),
                                                "points": ZERO, "amount": ZERO, "rows": []})
        month["points"] += row["estimate"]
        month["amount"] += row["amount"]
        month["rows"].append(row)
    out = []
    for fy in sorted(years):
        year = years[fy]
        year["months"] = [year["months"][k] for k in sorted(year["months"])]
        out.append(year)
    return {"total": total, "years": out,
            "count": sum(len(m["rows"]) for y in out for m in y["months"])}


def _today():
    from django.utils import timezone

    return timezone.localdate()


def period_totals(rule, employee, on_date, *, exclude_pk=None, is_health=False):
    """What this employee has actually banked in the rule's current period.

    Returns (volume, bonus_released, points_booked) across their APPROVED sales
    of the rule's product. Health counts Fresh only, one year of a multiyear
    premium at a time — Port pays its own flat rate and must never lift the band.
    """
    from django.db.models import Sum

    from ..models import BonusPayout, Sale
    from ..models.sales import _ANNUAL_SLICE

    start, end = period_bounds(rule, on_date)
    qs = Sale.objects.filter(
        employee=employee, status=Sale.STATUS_APPROVED,
        date__gte=start, date__lte=end,
    )
    qs = qs.filter(rule_sales_q(rule))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    if is_health:
        volume = qs.exclude(policy_type="port").aggregate(t=Sum(_ANNUAL_SLICE))["t"]
    else:
        volume = qs.aggregate(t=Sum("amount"))["t"]
    agg = qs.aggregate(b=Sum("bonus_points"), p=Sum("points"))
    # A bonus recorded as paid by hand is prize money already in the employee's
    # hands, so the ladder releases only the rest. Nothing else is ever netted
    # off: the prize is what the year's volume reaches, whatever any single
    # month did.
    advances = sum(
        (p.amount for p in BonusPayout.objects.filter(
            employee=employee, rule=rule, for_month__gte=start, for_month__lte=end)),
        ZERO,
    )
    return volume or ZERO, (agg["b"] or ZERO) + advances, agg["p"] or ZERO


def rate_for_volume(rule, volume):
    """The rate-mode band a cumulative volume lands in."""
    from ..models import IncentiveRule

    if rule is None or rule.slab_mode != IncentiveRule.MODE_RATE:
        return unit_rate_percent(rule)
    band = next((s for s in rule.slabs.all().order_by("-threshold")
                 if Decimal(str(volume or 0)) >= s.threshold), None)
    return band.payout if band else unit_rate_percent(rule)


def next_rung(rule, volume):
    """The next rung above `volume` and what reaching it is worth right now.

    For a bonus ladder that is the extra bonus released; for rate bands it is
    what re-rating the whole period at the higher band adds. None at the top.
    """
    from ..models import IncentiveRule

    if rule is None:
        return None
    volume = Decimal(str(volume or 0))
    ahead = [s for s in rule.slabs.all().order_by("threshold") if s.threshold > volume]
    if not ahead:
        return None
    s = ahead[0]
    if rule.slab_mode == IncentiveRule.MODE_RATE:
        worth = (s.threshold * s.payout / Decimal("100")
                 - volume * rate_for_volume(rule, volume) / Decimal("100"))
    else:
        worth = s.payout - bonus_released_for(rule, volume)
    return {"slab": s, "gap": s.threshold - volume, "worth": max(worth, ZERO)}


def project(rule, actual_volume, added_amount, *, added_port=ZERO):
    """What adding `added_amount` of business to `actual_volume` would pay.

    Rate bands re-rate the *whole* period when volume crosses, so the honest
    answer is the period's value at the new volume minus its value now — not a
    per-sale rate. Bonus ladders add their base on the new business plus
    whatever extra the ladder releases.
    """
    from ..models import IncentiveRule

    actual_volume = Decimal(str(actual_volume or 0))
    added_amount = Decimal(str(added_amount or 0))
    added_port = Decimal(str(added_port or 0))
    final = actual_volume + added_amount
    port_pay = ZERO          # Port earns nothing; kept so the shape doesn't change.

    if rule is None or not rule.active:
        return {"added": ZERO, "final_volume": final, "rate": ZERO,
                "base": ZERO, "bonus": ZERO, "port": ZERO}

    if rule.slab_mode == IncentiveRule.MODE_RATE and rule.slabs.exists():
        now = actual_volume * rate_for_volume(rule, actual_volume) / Decimal("100")
        after = final * rate_for_volume(rule, final) / Decimal("100")
        added = after - now
        return {"added": added + port_pay, "final_volume": final,
                "rate": rate_for_volume(rule, final), "base": added,
                "bonus": ZERO, "port": port_pay}

    base = added_amount * unit_rate_percent(rule) / Decimal("100")
    bonus = bonus_released_for(rule, final) - bonus_released_for(rule, actual_volume)
    return {"added": base + max(bonus, ZERO) + port_pay, "final_volume": final,
            "rate": unit_rate_percent(rule), "base": base,
            "bonus": max(bonus, ZERO), "port": port_pay}


def bonus_released_for(rule, volume):
    """Bonus a period would already have released by the time it reached
    `volume` — the payout of the highest rung that volume clears."""
    from ..models import IncentiveRule

    if rule is None or rule.slab_mode != IncentiveRule.MODE_BONUS:
        return ZERO
    volume = Decimal(str(volume or 0))
    band = next((s for s in rule.slabs.all().order_by("-threshold") if volume >= s.threshold), None)
    return band.payout if band else ZERO


def points_on(rule, amount, *, volume=None, policy_type="", is_health=False):
    """Points a sale of `amount` earns — the plain number, for worked examples.

    `volume` is the period total the band should be read at; it defaults to the
    sale standing alone.
    """
    amount = Decimal(str(amount or 0))
    prior = ZERO if volume is None else Decimal(str(volume)) - amount
    return quote(rule, amount, prior_volume=max(prior, ZERO),
                 prior_bonus=bonus_released_for(rule, max(prior, ZERO)),
                 policy_type=policy_type, is_health=is_health)["total"]


def walkthrough(rule, is_health=False):
    """A worked example built from the rule's own rungs.

    Generated rather than written down, so the story on the page moves if the
    rates ever do instead of quietly going stale.
    """
    from ..models import IncentiveRule

    slabs = sorted(rule.slabs.all(), key=lambda s: s.threshold)
    if not slabs:
        return []

    if rule.slab_mode == IncentiveRule.MODE_RATE:
        # Two sales, the second crossing the first real step, to show the
        # earlier sale being pulled up with it.
        step = next((s.threshold for s in slabs if s.threshold > 0), None)
        if step is None:
            return []
        first = (step * Decimal("0.8")).quantize(Decimal("1"))
        second = (step * Decimal("0.4")).quantize(Decimal("1"))
        r1 = rate_for_volume(rule, first)
        r2 = rate_for_volume(rule, first + second)
        return [
            {"amount": first, "running": first, "rate": r1,
             "this_sale": first * r1 / Decimal("100"),
             "earlier_now": None,
             "month_total": first * r1 / Decimal("100")},
            {"amount": second, "running": first + second, "rate": r2,
             "this_sale": second * r2 / Decimal("100"),
             "earlier_now": first * r2 / Decimal("100"),
             "month_total": (first + second) * r2 / Decimal("100")},
        ]

    # Bonus ladder: a year that climbs through the first three rungs.
    base_rate = unit_rate_percent(rule)
    targets = [(slabs[0].threshold * Decimal("0.4")).quantize(Decimal("1"))]
    for s in slabs[:3]:
        targets.append(s.threshold)
    rows = []
    prev_total = ZERO
    prev_prize = ZERO
    for target in targets:
        amount = target - prev_total
        if amount <= 0:
            continue
        prize = bonus_released_for(rule, target)
        rows.append({
            "amount": amount,
            "base": amount * base_rate / Decimal("100"),
            "running": target,
            "prize_level": prize,
            "bonus_paid": prize - prev_prize,
        })
        prev_total = target
        prev_prize = prize
    return rows


def explain(rule, is_health=False):
    """A plain-language description of one product's structure, in points.

    Deliberately free of firm margin/commission figures — this is read by the
    people who sell, and what they need is what they earn.
    """
    from ..models import IncentiveRule

    if rule is None or not rule.active:
        return None
    name = rule.product_ref.name if rule.product_ref_id else rule.product
    slabs = sorted(rule.slabs.all(), key=lambda s: s.threshold)
    window = "this month" if rule.slab_period == IncentiveRule.PERIOD_MONTH else "this financial year"

    # An example sale big enough to be recognisable for the product.
    sample = Decimal("100000") if rule.unit_amount >= Decimal("100000") else Decimal("25000")

    out = {"name": name, "rule": rule, "is_health": is_health, "window": window,
           "kind": "flat", "examples": [], "rungs": [], "notes": [],
           "prize_intro": "", "walkthrough": walkthrough(rule, is_health=is_health)}

    if slabs and rule.slab_mode == IncentiveRule.MODE_RATE:
        out["kind"] = "bands"
        out["headline"] = (
            f"Your whole month decides the rate. Add up all the {name.lower()} you sell "
            f"in the month — that one total picks a row in the table below, and every "
            f"rupee of the month earns at that row. Not the sale, the month."
        )
        # Read as ranges ("₹25,000 to ₹50,000"), which is how people think about
        # where their month stands, and priced per ₹1,00,000 — the unit the rest
        # of the app quotes. NOT "points for a ₹1,00,000 sale": a sale that size
        # would push the month into a higher row and every line would collapse
        # to the same number.
        for i, s in enumerate(slabs):
            nxt = slabs[i + 1].threshold if i + 1 < len(slabs) else None
            out["rungs"].append({
                "from": s.threshold,
                "upto": nxt,
                "per_lakh": s.payout * Decimal("1000"),
            })
    elif slabs:
        out["kind"] = "ladder"
        base_pts = points_on(rule, sample)
        # A ₹1,00,000 policy sits below the first rung, so this is base only.
        per_lakh = Decimal("100000") * unit_rate_percent(rule) / Decimal("100")
        out["headline"] = (
            f"There are two separate payments here, and they have nothing to do with "
            f"each other. First, a base on every single policy: a ₹{_n(sample)} policy "
            f"earns {_n(base_pts)} points, ₹1,00,000 earns {_n(per_lakh)}. "
            f"No minimum, nothing to reach first. Second, one prize for the whole year."
        )
        out["prize_intro"] = (
            "The prize is a single reward for the year — not one per policy, and not one "
            "per step. How much you do between April and March decides how big it gets. "
            "The table below is the price list."
        )
        for s in slabs:
            out["rungs"].append({
                "from": s.threshold,
                "bonus": s.payout,
                # The prize is added here, not by points_on(): a sale only ever
                # carries the base now, so the price list has to say what the
                # year is worth all in.
                "total_at": points_on(rule, s.threshold) + s.payout,
            })
        top = slabs[2] if len(slabs) > 2 else slabs[-1]
        out["notes"].append(
            f"Whatever level you finish the year on is the whole prize. Reach "
            f"₹{_n(top.threshold)} and your prize is {_n(top.payout)} points — not "
            f"{' + '.join(_n(x.payout) for x in slabs[:3])} added together. "
            f"You are never paid the same level twice."
        )
        out["notes"].append(
            "The prize is separate money, handed over to you directly — it is not added "
            "to a policy's points. Your ladder shows the level your year has reached and "
            "how much of it has already been given to you."
        )
        out["notes"].append(
            "On 1 April the year's total and the prize both reset to zero. The base does "
            "not reset — it pays from your very first policy of the new year."
        )
        out["notes"].append(
            f"Never reach ₹{_n(slabs[0].threshold)} in a year? The prize is zero, but you "
            f"still earned the base on every policy you wrote."
        )
        out["notes"].append(
            "Only the year's total decides the prize. It makes no difference whether it "
            "arrives in a few big months or a lot of small ones, and a big month is never "
            "deducted from anything."
        )
    else:
        out["kind"] = "flat"
        pts = points_on(rule, sample)
        out["headline"] = (f"A flat rate on every sale. ₹{_n(sample)} of {name.lower()} "
                           f"earns {_n(pts)} points, and twice that earns twice the points.")
        out["examples"] = [
            {"amount": a, "points": points_on(rule, a)}
            for a in (sample, sample * 2, sample * 10)
        ]

    if is_health:
        out["notes"].insert(0,
            "It works downwards too. If a sale is rejected or removed and your month "
            "falls back below a step, the rest of the month follows it back down."
        )
        out["notes"].append(
            "A Port policy earns no points, and it does not count towards the monthly "
            "total that sets your step either — only Fresh business moves your step."
        )
    return out


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

    # Port earns nothing. This branch has to stay: without it a Port sale would
    # fall through to the Fresh band below and be paid at the month's rate,
    # which is the opposite of the rule.
    if is_health and policy_type == "port":
        return {"base": ZERO, "bonus": ZERO, "total": ZERO, "rate": ZERO,
                "volume": amount, "band": None,
                "basis": "Port policy — earns no points."}

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
    # The yearly prize is handed over as cash and recorded on the Life Bonus
    # page; a sale never releases it. Auto-releasing pinned the whole rung on
    # whichever sale happened to cross it — a ₹1L April sale showing a ₹3,000
    # release — and paid it twice once the cash was recorded.
    fy_prize = rule.slab_period == IncentiveRule.PERIOD_FY
    if slabs:
        band = next((s for s in slabs if volume >= s.threshold), None)
        payout = band.payout if band else ZERO
        if not fy_prize:
            bonus = max(payout - prior_bonus, ZERO)
    window = "this month" if rule.slab_period == IncentiveRule.PERIOD_MONTH else "this financial year"
    basis = f"Base {_pct(rate)}% of ₹{_n(amount)}."
    if slabs:
        if fy_prize and band is not None:
            basis += (f" Cumulative ₹{_n(volume)} {window} stands at the "
                      f"₹{_n(band.threshold)} rung, worth ₹{_n(band.payout)} for the year — "
                      f"prize money is paid by hand and recorded on the Life Bonus page, "
                      f"never through a sale.")
        elif bonus > 0:
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
    # Indian digit grouping, same as the template filter — 2063297 -> 20,63,297.
    from ..templatetags.custom_filters import inr

    return inr(v)


def _pct(v):
    v = Decimal(str(v or 0))
    return f"{v.normalize():f}" if v == v.to_integral_value() else f"{v:.2f}".rstrip("0").rstrip(".")

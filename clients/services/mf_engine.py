"""Mutual Fund Distributor (MFD) revenue projection engine.

Phase 1: manual monthly-actuals ledger drives a month-by-month forward
simulation (default 120 months). This is a recurring-revenue / AUM
compounding projection — deliberately a business-intelligence tool, not an
accounting or broker-reconciliation system.

All simulation math runs in float (projections are estimates); inputs and
outputs are Decimals. Nothing here mutates the database.
"""
from __future__ import annotations

from calendar import month_abbr
from decimal import Decimal


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _monthly_rate(annual_pct) -> float:
    """Geometric monthly rate from an annual percentage."""
    g = _f(annual_pct) / 100.0
    if g <= -1.0:
        return -1.0
    return (1.0 + g) ** (1.0 / 12.0) - 1.0


def _q(x) -> Decimal:
    return Decimal(str(round(_f(x), 2)))


def _pct(x) -> Decimal:
    return Decimal(str(round(_f(x), 2)))


def realized_metrics(snapshot):
    """Current (this-month) realised position from the latest snapshot."""
    return {
        "total_aum": _q(snapshot.total_aum),
        "equity_aum": _q(snapshot.equity_aum),
        "debt_aum": _q(snapshot.debt_aum),
        "hybrid_aum": _q(snapshot.hybrid_aum),
        "monthly_trail": _q(snapshot.effective_monthly_trail),
        "estimated_monthly_trail": _q(snapshot.monthly_trail),
        "annualized_trail": _q(snapshot.annualized_trail),
        "actual_insurance_revenue": _q(snapshot.actual_insurance_revenue),
        "trail_is_actual": snapshot.actual_trail_income is not None,
        "new_sip": _q(snapshot.new_sip),
        "new_lumpsum": _q(snapshot.new_lumpsum),
        "sip_book": _q(snapshot.sip_book),
        # Trail split by product type for the breakdown chart.
        "trail_equity": _q(_f(snapshot.equity_aum) * _f(snapshot.equity_trail_pct) / 100.0 / 12.0),
        "trail_debt": _q(_f(snapshot.debt_aum) * _f(snapshot.debt_trail_pct) / 100.0 / 12.0),
        "trail_hybrid": _q(_f(snapshot.hybrid_aum) * _f(snapshot.hybrid_trail_pct) / 100.0 / 12.0),
    }


def project(snapshot, months: int = 120, include_new_business: bool = True):
    """Month-by-month simulation forward from `snapshot`.

    Existing AUM (equity/debt/hybrid) compounds at the assumed market
    growth and leaks at the assumed redemption rate, each generating trail
    at its own rate. The SIP book attrites by the stoppage rate and (when
    `include_new_business`) is topped up by continued monthly SIP
    mobilisation; SIP inflow + lump sum accumulate into a new-business
    corpus that compounds and earns the blended projection trail.

    Returns a list of monthly points (length == months).
    """
    g_m = _monthly_rate(snapshot.annual_market_growth_pct)
    red_m = _f(snapshot.redemption_rate_pct) / 100.0 / 12.0
    stop_m = _f(snapshot.sip_stoppage_rate_pct) / 100.0 / 12.0

    eq = _f(snapshot.equity_aum)
    dt = _f(snapshot.debt_aum)
    hy = _f(snapshot.hybrid_aum)
    new_aum = 0.0
    sip_book = _f(snapshot.sip_book)

    new_sip = _f(snapshot.new_sip) if include_new_business else 0.0
    new_lump = _f(snapshot.new_lumpsum) if include_new_business else 0.0

    eq_t = _f(snapshot.equity_trail_pct) / 100.0 / 12.0
    dt_t = _f(snapshot.debt_trail_pct) / 100.0 / 12.0
    hy_t = _f(snapshot.hybrid_trail_pct) / 100.0 / 12.0
    new_t = _f(snapshot.projection_trail_pct) / 100.0 / 12.0

    yr, mo = int(snapshot.year), int(snapshot.month)
    series = []
    cumulative_trail = 0.0

    for i in range(1, months + 1):
        # advance calendar month
        mo += 1
        if mo > 12:
            mo = 1
            yr += 1

        # 1) market growth on every bucket
        eq *= (1 + g_m); dt *= (1 + g_m); hy *= (1 + g_m); new_aum *= (1 + g_m)
        # 2) redemptions (annual rate spread monthly) — not on this month's fresh money
        eq -= eq * red_m; dt -= dt * red_m; hy -= hy * red_m; new_aum -= new_aum * red_m
        eq = max(eq, 0.0); dt = max(dt, 0.0); hy = max(hy, 0.0); new_aum = max(new_aum, 0.0)
        # 3) SIP book: existing attrites by stoppage, continued mobilisation adds
        sip_book = max(sip_book * (1 - stop_m) + new_sip, 0.0)
        # 4) this month's contributions accumulate into the new-business corpus
        new_aum += sip_book + new_lump

        total_aum = eq + dt + hy + new_aum
        monthly_trail = eq * eq_t + dt * dt_t + hy * hy_t + new_aum * new_t
        cumulative_trail += monthly_trail

        series.append({
            "idx": i,
            "year": yr,
            "month": mo,
            "label": f"{month_abbr[mo]} {yr}",
            "total_aum": _q(total_aum),
            "equity_aum": _q(eq),
            "debt_aum": _q(dt),
            "hybrid_aum": _q(hy),
            "new_aum": _q(new_aum),
            "sip_book": _q(sip_book),
            "monthly_trail": _q(monthly_trail),
            "annual_trail": _q(monthly_trail * 12),
            "cumulative_trail": _q(cumulative_trail),
        })

    return series


def reconcile(curr, prev):
    """Decompose this month's AUM change into operational vs market.

    Opening AUM = the previous month's closing AUM (source-of-truth chain).
    Operational growth = net of SIP + lump sum − redemptions.
    Market movement = actual closing − (opening + operational); this is the
    NAV/volatility residual, recorded as market impact, not an operational
    miss. Also compares last month's 1-month forecast to this actual.
    """
    closing = _f(curr.total_aum)
    operational = _f(curr.new_sip) + _f(curr.new_lumpsum) - _f(curr.redemptions)

    out = {
        "has_prev": prev is not None,
        "opening_aum": _q(prev.total_aum) if prev else None,
        "closing_aum": _q(closing),
        "operational_growth": _q(operational),
    }
    if prev is None:
        out.update({k: None for k in (
            "expected_operational_aum", "market_movement_impact", "net_aum_growth",
            "market_movement_pct", "revenue_impact_from_market",
            "projected_aum", "projection_accuracy", "projection_variance",
        )})
        return out

    opening = _f(prev.total_aum)
    expected = opening + operational            # before any market move
    market_impact = closing - expected
    net_growth = closing - opening
    market_pct = (market_impact / expected * 100.0) if expected else 0.0

    # Blended effective annual trail on the actual book → ₹ trail gained/lost
    # purely because of the market move this month.
    blended_annual = (
        _f(curr.annualized_trail) / closing * 100.0
        if closing else _f(curr.projection_trail_pct)
    )
    rev_impact = market_impact * blended_annual / 100.0 / 12.0

    # Forecast made last month for this month (prev's 1-month projection).
    projected = _f(project(prev, months=1)[0]["total_aum"])
    variance = closing - projected
    accuracy = 100.0 - (abs(variance) / closing * 100.0) if closing else 0.0
    accuracy = max(accuracy, 0.0)

    out.update({
        "expected_operational_aum": _q(expected),
        "market_movement_impact": _q(market_impact),
        "net_aum_growth": _q(net_growth),
        "market_movement_pct": _pct(market_pct),
        "revenue_impact_from_market": _q(rev_impact),
        "projected_aum": _q(projected),
        "projection_variance": _q(variance),
        "projection_accuracy": _pct(accuracy),
    })
    return out


def _at(series, idx):
    """1-based month index lookup, clamped to the series length."""
    if not series:
        return None
    return series[min(idx, len(series)) - 1]


def build_dashboard(snapshot, horizon_months: int = 120):
    """Assemble every metric/series the dashboard needs from one snapshot."""
    realized = realized_metrics(snapshot)
    full = project(snapshot, months=horizon_months, include_new_business=True)
    # Embedded = revenue already created by today's book + committed SIPs,
    # i.e. NO new mobilisation from here on.
    embedded = project(snapshot, months=horizon_months, include_new_business=False)

    def milestone(series, idx):
        p = _at(series, idx)
        return {
            "aum": p["total_aum"] if p else Decimal("0.00"),
            "monthly_trail": p["monthly_trail"] if p else Decimal("0.00"),
            "annual_trail": p["annual_trail"] if p else Decimal("0.00"),
        }

    projections = {
        "y1": milestone(full, 12),
        "y3": milestone(full, 36),
        "y5": milestone(full, 60),
        "y10": milestone(full, 120),
    }
    embedded_future = {
        "y1": _at(embedded, 12)["cumulative_trail"] if _at(embedded, 12) else Decimal("0.00"),
        "y3": _at(embedded, 36)["cumulative_trail"] if _at(embedded, 36) else Decimal("0.00"),
        "y5": _at(embedded, 60)["cumulative_trail"] if _at(embedded, 60) else Decimal("0.00"),
    }

    # Chart series (down to floats for JSON / Chart.js).
    def fnum(d):
        return float(d)

    labels = [p["label"] for p in full]
    charts = {
        "labels": labels,
        "aum": [fnum(p["total_aum"]) for p in full],
        "monthly_revenue": [fnum(p["monthly_trail"]) for p in full],
        "sip_book": [fnum(p["sip_book"]) for p in full],
        "cumulative_revenue": [fnum(p["cumulative_trail"]) for p in full],
        "embedded_cumulative": [fnum(p["cumulative_trail"]) for p in embedded],
        "breakdown": {
            "labels": ["Equity", "Debt", "Hybrid", "New / SIP-built"],
            "values": [
                fnum(realized["trail_equity"]),
                fnum(realized["trail_debt"]),
                fnum(realized["trail_hybrid"]),
                0.0,  # new corpus is zero at the current month; grows in projection
            ],
        },
    }

    # Recurring vs upfront: the MFD trail model is entirely recurring;
    # no upfront brokerage is modelled in Phase 1.
    recurring_vs_upfront = {
        "recurring_monthly": realized["monthly_trail"],
        "upfront_monthly": Decimal("0.00"),
    }

    return {
        "realized": realized,
        "projections": projections,
        "embedded_future": embedded_future,
        "recurring_vs_upfront": recurring_vs_upfront,
        "total_business_revenue_annual": realized["annualized_trail"],
        "charts": charts,
        "milestone_rows": [
            {"label": "1 Year", "p": _at(full, 12)},
            {"label": "3 Years", "p": _at(full, 36)},
            {"label": "5 Years", "p": _at(full, 60)},
            {"label": "10 Years", "p": _at(full, 120)},
        ],
    }

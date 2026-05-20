"""Mutual Fund Distributor (MFD) revenue & historical-analytics engine.

The MF book is recorded as a series of time-range snapshots (`MFSnapshot`).
Each snapshot covers an arbitrary date range (a month, a quarter, a
backfilled year, etc.) and stores period actuals only — forward
assumptions live on `MFProjectionSettings`.

This module computes three families of output:

* `realized_metrics(snap)`  — period-level KPIs for one snapshot.
* `reconcile(curr, prev)`   — operational vs market decomposition between
                              two consecutive snapshots.
* `historical_analytics(snapshots)` — long-run business intelligence over
                              the whole snapshot ledger (persistency,
                              CAGR, volatility, retention, …).
* `build_dashboard(snap, settings)` — 120-month forward projection from
                              the latest snapshot using shared settings.

All simulation math runs in float (projections are estimates); inputs and
outputs are Decimals. Nothing here mutates the database.
"""
from __future__ import annotations

from calendar import month_abbr
from datetime import date, timedelta
from decimal import Decimal
import math


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _q(x) -> Decimal:
    return Decimal(str(round(_f(x), 2)))


def _qn(x):
    """Quantize but preserve None — used wherever 'unknown' is meaningful."""
    return None if x is None else _q(x)


def _pct(x) -> Decimal:
    return Decimal(str(round(_f(x), 2)))


def _monthly_rate(annual_pct) -> float:
    g = _f(annual_pct) / 100.0
    if g <= -1.0:
        return -1.0
    return (1.0 + g) ** (1.0 / 12.0) - 1.0


# ---------------- per-snapshot ----------------

def realized_metrics(snap):
    """KPIs for a single time-range snapshot."""
    months = _f(snap.months_in_period)
    trail_per_month = _f(snap.trail_income) / months if months else 0.0
    insurance_per_month = (
        _f(snap.insurance_renewals) + _f(snap.insurance_new_business)
    ) / months if months else 0.0
    return {
        "label": f"{snap.start_date:%d-%b-%Y} → {snap.end_date:%d-%b-%Y}",
        "days": int(_f(snap.days_in_period)),
        "months": _q(months),
        "opening_aum": _qn(snap.opening_aum),
        "closing_aum": _qn(snap.closing_aum),
        "active_sip_book": _q(snap.active_sip_book),
        "gross_sip_registered": _q(snap.gross_sip_registered),
        "stopped_sip_amount": _q(snap.stopped_sip_amount),
        "net_sip_growth": _q(snap.net_sip_growth),
        "new_lumpsum": _q(snap.new_lumpsum),
        "redemptions": _q(snap.redemptions),
        "trail_income": _q(snap.trail_income),
        "trail_income_per_month": _q(trail_per_month),
        "insurance_new_business": _q(snap.insurance_new_business),
        "insurance_renewals": _q(snap.insurance_renewals),
        "insurance_per_month": _q(insurance_per_month),
        "total_recurring_revenue": _q(snap.total_recurring_revenue),
        "sip_collected": _q(snap.sip_collected),
        "operational_inflow": _q(snap.operational_inflow),
        "expected_operational_aum": _qn(snap.expected_operational_aum),
        "market_impact": _qn(snap.market_impact),
        "net_aum_growth": _qn(snap.net_aum_growth),
        "has_aum": snap.opening_aum is not None and snap.closing_aum is not None,
    }


# ---------------- two-snapshot reconciliation ----------------

def reconcile(curr, prev):
    """Decompose this snapshot's AUM change into operational vs market.

    Operational growth is always computable (it's just flows). The
    market/net/accuracy decomposition needs opening + closing AUM; if
    either is missing, those keys return None and the operational figure
    still surfaces.
    """
    operational = _f(curr.operational_inflow)
    has_aum = curr.opening_aum is not None and curr.closing_aum is not None

    out = {
        "has_prev": prev is not None,
        "has_aum": has_aum,
        "opening_aum": _qn(curr.opening_aum),
        "closing_aum": _qn(curr.closing_aum),
        "operational_growth": _q(operational),
    }

    if not has_aum:
        out.update({k: None for k in (
            "expected_operational_aum", "market_movement_impact",
            "market_movement_pct", "net_aum_growth",
            "revenue_impact_from_market",
            "projected_aum", "projection_variance", "projection_accuracy",
        )})
        return out

    closing = _f(curr.closing_aum)
    opening = _f(curr.opening_aum)
    expected = opening + operational
    market_impact = closing - expected
    net_growth = closing - opening
    market_pct = (market_impact / expected * 100.0) if expected else 0.0

    months = _f(curr.months_in_period) or 1.0
    blended_annual = (
        _f(curr.trail_income) / months * 12.0 / closing * 100.0
        if closing and _f(curr.trail_income) else 0.0
    )
    rev_impact = market_impact * blended_annual / 100.0 / 12.0 * months

    out.update({
        "expected_operational_aum": _q(expected),
        "market_movement_impact": _q(market_impact),
        "market_movement_pct": _pct(market_pct),
        "net_aum_growth": _q(net_growth),
        "revenue_impact_from_market": _q(rev_impact),
    })

    if prev is None or prev.closing_aum is None:
        out.update(projected_aum=None, projection_variance=None,
                   projection_accuracy=None)
        return out

    # Forecast: project prev's closing forward for the current period
    # length using the global projection settings (not stored on snapshot).
    from clients.models import MFProjectionSettings
    settings = MFProjectionSettings.current()
    g_m = _monthly_rate(settings.annual_market_growth_pct)
    red_m = _f(settings.redemption_rate_pct) / 100.0 / 12.0
    proj_months = max(int(round(_f(curr.months_in_period))), 1)
    pa = _f(prev.closing_aum)
    sip_book_proj = _f(prev.active_sip_book)
    new_sip_proj = _f(prev.gross_sip_registered) / max(_f(prev.months_in_period), 1.0)
    new_lump_proj = _f(prev.new_lumpsum) / max(_f(prev.months_in_period), 1.0)
    stop_m = _f(settings.sip_stoppage_rate_pct) / 100.0 / 12.0
    for _ in range(proj_months):
        pa = pa * (1 + g_m) - pa * red_m
        sip_book_proj = max(sip_book_proj * (1 - stop_m) + new_sip_proj, 0.0)
        pa += sip_book_proj + new_lump_proj
    projected = pa
    variance = closing - projected
    accuracy = 100.0 - (abs(variance) / closing * 100.0) if closing else 0.0
    accuracy = max(accuracy, 0.0)
    out.update(
        projected_aum=_q(projected),
        projection_variance=_q(variance),
        projection_accuracy=_pct(accuracy),
    )
    return out


# ---------------- forward projection ----------------

def project(snap, settings, months: int = 120, include_new_business: bool = True):
    """Month-by-month forward simulation from a snapshot, using global settings."""
    g_m = _monthly_rate(settings.annual_market_growth_pct)
    red_m = _f(settings.redemption_rate_pct) / 100.0 / 12.0
    stop_m = _f(settings.sip_stoppage_rate_pct) / 100.0 / 12.0
    proj_trail = _f(settings.projection_trail_pct) / 100.0 / 12.0

    aum = _f(snap.closing_aum)
    new_aum = 0.0
    sip_book = _f(snap.active_sip_book)
    period_months = max(_f(snap.months_in_period), 1.0)
    new_sip = (_f(snap.gross_sip_registered) / period_months) if include_new_business else 0.0
    new_lump = (_f(snap.new_lumpsum) / period_months) if include_new_business else 0.0
    trail_monthly_anchor = _f(snap.trail_income) / period_months
    blended_annual = (trail_monthly_anchor * 12.0 / aum * 100.0) if aum else _f(settings.projection_trail_pct)
    existing_trail_m = blended_annual / 100.0 / 12.0

    yr, mo = snap.end_date.year, snap.end_date.month
    series = []
    cumulative_trail = 0.0

    for i in range(1, months + 1):
        mo += 1
        if mo > 12:
            mo = 1; yr += 1
        aum *= (1 + g_m); new_aum *= (1 + g_m)
        aum -= aum * red_m; new_aum -= new_aum * red_m
        aum = max(aum, 0.0); new_aum = max(new_aum, 0.0)
        sip_book = max(sip_book * (1 - stop_m) + new_sip, 0.0)
        new_aum += sip_book + new_lump
        total = aum + new_aum
        trail = aum * existing_trail_m + new_aum * proj_trail
        cumulative_trail += trail
        series.append({
            "idx": i, "year": yr, "month": mo,
            "label": f"{month_abbr[mo]} {yr}",
            "total_aum": _q(total),
            "existing_aum": _q(aum),
            "new_aum": _q(new_aum),
            "sip_book": _q(sip_book),
            "monthly_trail": _q(trail),
            "annual_trail": _q(trail * 12),
            "cumulative_trail": _q(cumulative_trail),
        })
    return series


def _at(series, idx):
    if not series:
        return None
    return series[min(idx, len(series)) - 1]


def build_dashboard(snap, settings, horizon_months: int = 120, projection_anchor=None):
    """Realised metrics come from `snap`; the 120-month projection runs from
    `projection_anchor` (defaults to `snap`). If the anchor has no closing
    AUM, projection blocks are returned empty and the template hides them.
    """
    realized = realized_metrics(snap)
    anchor = projection_anchor or snap
    has_anchor_aum = anchor is not None and anchor.closing_aum is not None

    months = _f(snap.months_in_period) or 1.0
    trail_per_month = _f(snap.trail_income) / months
    out = {
        "realized": realized,
        "recurring_vs_upfront": {
            "recurring_monthly": _q(trail_per_month + _f(snap.insurance_renewals) / months),
            "upfront_monthly": _q(_f(snap.insurance_new_business) / months),
        },
        "total_business_revenue_annual": _q((trail_per_month + _f(snap.insurance_renewals) / months) * 12),
        "has_anchor_aum": has_anchor_aum,
        "anchor_label": (f"{anchor.start_date:%d-%b-%Y} → {anchor.end_date:%d-%b-%Y}" if anchor else None),
    }

    if not has_anchor_aum:
        out.update({
            "projections": None, "embedded_future": None,
            "charts": None, "milestone_rows": [],
        })
        return out

    full = project(anchor, settings, months=horizon_months, include_new_business=True)
    embedded = project(anchor, settings, months=horizon_months, include_new_business=False)

    def milestone(series, idx):
        p = _at(series, idx)
        return {
            "aum": p["total_aum"] if p else Decimal("0.00"),
            "monthly_trail": p["monthly_trail"] if p else Decimal("0.00"),
            "annual_trail": p["annual_trail"] if p else Decimal("0.00"),
        }

    out["projections"] = {k: milestone(full, n) for k, n in
                          (("y1", 12), ("y3", 36), ("y5", 60), ("y10", 120))}
    out["embedded_future"] = {
        "y1": _at(embedded, 12)["cumulative_trail"] if _at(embedded, 12) else Decimal("0.00"),
        "y3": _at(embedded, 36)["cumulative_trail"] if _at(embedded, 36) else Decimal("0.00"),
        "y5": _at(embedded, 60)["cumulative_trail"] if _at(embedded, 60) else Decimal("0.00"),
    }
    out["charts"] = {
        "labels": [p["label"] for p in full],
        "aum": [float(p["total_aum"]) for p in full],
        "monthly_revenue": [float(p["monthly_trail"]) for p in full],
        "sip_book": [float(p["sip_book"]) for p in full],
        "cumulative_revenue": [float(p["cumulative_trail"]) for p in full],
        "embedded_cumulative": [float(p["cumulative_trail"]) for p in embedded],
    }
    out["milestone_rows"] = [
        {"label": "1 Year", "p": _at(full, 12)},
        {"label": "3 Years", "p": _at(full, 36)},
        {"label": "5 Years", "p": _at(full, 60)},
        {"label": "10 Years", "p": _at(full, 120)},
    ]
    return out


# ---------------- historical analytics across all snapshots ----------------

def _stddev(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def historical_analytics(snapshots):
    """Aggregate the snapshot ledger into long-run business KPIs.

    `snapshots` is expected oldest → newest. Missing data is tolerated:
    metrics with no denominator return None.
    """
    snaps = list(snapshots)
    out = {
        "has_data": bool(snaps),
        "period_count": len(snaps),
        "first": snaps[0].start_date if snaps else None,
        "last": snaps[-1].end_date if snaps else None,
    }
    if not snaps:
        return out

    first, last = snaps[0], snaps[-1]
    total_days = (last.end_date - first.start_date).days + 1
    years = total_days / 365.25

    gross_sip = sum(_f(s.gross_sip_registered) for s in snaps)
    stopped = sum(_f(s.stopped_sip_amount) for s in snaps)
    sip_collected = sum(_f(s.sip_collected) for s in snaps)
    lumpsum = sum(_f(s.new_lumpsum) for s in snaps)
    redemptions = sum(_f(s.redemptions) for s in snaps)
    trail_total = sum(_f(s.trail_income) for s in snaps)
    insurance_renewal = sum(_f(s.insurance_renewals) for s in snaps)
    market_impacts = [_f(s.market_impact) for s in snaps]
    operational_inflows = [_f(s.operational_inflow) for s in snaps]

    # ---- per-period rates, then a time-weighted average across snapshots ----
    # Each rate is computed *within* a period and weighted by months_in_period
    # so a 12-month snapshot counts more than a 1-month one.
    def _wmean(samples):
        """samples = list of (rate, weight). Returns weighted mean or None."""
        total_w = sum(w for _, w in samples)
        if not samples or total_w <= 0:
            return None
        return sum(r * w for r, w in samples) / total_w

    persistency_samples = []      # active_sip_book / (opening_book + gross)
    stoppage_samples = []         # stopped / (opening_book + gross)
    redemption_samples = []       # redemptions / (sip_collected + lumpsum)
    aum_retention_samples = []    # closing / (opening + operational)
    rev_per_month_growth_samples = []  # period-over-period trail/mo growth

    prev_sip_book = None
    prev_trail_per_month = None
    for s in snaps:
        months = max(_f(s.months_in_period), 0.0)
        if months <= 0:
            continue

        opening_book = prev_sip_book if prev_sip_book is not None else (
            _f(s.active_sip_book) - (_f(s.gross_sip_registered) - _f(s.stopped_sip_amount))
        )
        opening_book = max(opening_book, 0.0)
        sip_base = opening_book + _f(s.gross_sip_registered)
        if sip_base > 0:
            persistency_samples.append((_f(s.active_sip_book) / sip_base * 100.0, months))
            stoppage_samples.append((_f(s.stopped_sip_amount) / sip_base * 100.0, months))

        period_inflows = _f(s.sip_collected) + _f(s.new_lumpsum)
        if period_inflows > 0:
            redemption_samples.append((_f(s.redemptions) / period_inflows * 100.0, months))

        if s.opening_aum is not None and s.closing_aum is not None:
            expected = _f(s.opening_aum) + _f(s.operational_inflow)
            if expected > 0:
                aum_retention_samples.append((_f(s.closing_aum) / expected * 100.0, months))

        per_month = _f(s.trail_income) / months
        if prev_trail_per_month is not None and prev_trail_per_month > 0:
            growth = (per_month - prev_trail_per_month) / prev_trail_per_month * 100.0
            rev_per_month_growth_samples.append((growth, months))
        prev_trail_per_month = per_month
        prev_sip_book = _f(s.active_sip_book)

    persistency = _wmean(persistency_samples)
    stoppage = _wmean(stoppage_samples)
    redemption_ratio = _wmean(redemption_samples)
    aum_retention = _wmean(aum_retention_samples)
    rev_growth = _wmean(rev_per_month_growth_samples)
    net_sip_growth = _f(last.active_sip_book) - _f(first.active_sip_book)
    net_inflow = sip_collected + lumpsum - redemptions

    per_month_trails = [
        _f(s.trail_income) / max(_f(s.months_in_period), 1.0) for s in snaps
    ]
    trail_mean = sum(per_month_trails) / len(per_month_trails) if per_month_trails else 0.0
    trail_stability = (
        100.0 - (_stddev(per_month_trails) / trail_mean * 100.0)
        if trail_mean > 0 else None
    )

    # AUM-dependent snapshot subset (still needed for op vs market totals + CAGR).
    aum_snaps = [s for s in snaps if s.opening_aum is not None and s.closing_aum is not None]
    aum_first = aum_snaps[0] if aum_snaps else None
    aum_last = aum_snaps[-1] if aum_snaps else None
    aum_operational = [_f(s.operational_inflow) for s in aum_snaps]

    # Op vs market totals only count periods where market impact is computable.
    op_total = sum(aum_operational)
    market_total = sum(_f(s.market_impact) for s in aum_snaps)
    op_vs_market = {"operational": _q(op_total), "market": _q(market_total)}

    cagr = None
    if aum_first and aum_last and _f(aum_first.opening_aum) > 0 and _f(aum_last.closing_aum) > 0:
        span_days = (aum_last.end_date - aum_first.start_date).days + 1
        span_years = span_days / 365.25
        if span_years > 0:
            cagr = ((_f(aum_last.closing_aum) / _f(aum_first.opening_aum)) ** (1.0 / span_years) - 1.0) * 100.0

    # Volatility = stddev of period-on-period closing AUM % change (AUM-aware snaps only).
    aum_changes = []
    for a, b in zip(aum_snaps, aum_snaps[1:]):
        if _f(a.closing_aum):
            aum_changes.append((_f(b.closing_aum) - _f(a.closing_aum)) / _f(a.closing_aum) * 100.0)
    volatility = _stddev(aum_changes) if aum_changes else None

    out.update({
        "years_covered": _pct(years),
        "sip_persistency_pct": _pct(persistency) if persistency is not None else None,
        "sip_stoppage_pct": _pct(stoppage) if stoppage is not None else None,
        "net_sip_growth": _q(net_sip_growth),
        "gross_sip_mobilisation": _q(gross_sip),
        "redemption_ratio_pct": _pct(redemption_ratio) if redemption_ratio is not None else None,
        "net_inflow": _q(net_inflow),
        "aum_retention_pct": _pct(aum_retention) if aum_retention is not None else None,
        "revenue_growth_pct": _pct(rev_growth) if rev_growth is not None else None,
        "trail_stability_pct": _pct(trail_stability) if trail_stability is not None else None,
        "operational_vs_market": op_vs_market,
        "historical_cagr_pct": _pct(cagr) if cagr is not None else None,
        "aum_volatility_pct": _pct(volatility) if volatility is not None else None,
        "total_trail_income": _q(trail_total),
        "total_insurance_renewals": _q(insurance_renewal),
    })
    return out

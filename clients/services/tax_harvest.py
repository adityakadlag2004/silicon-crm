"""LTCG tax harvesting: who is done this financial year, and who is pending.

Only clients with a harvest on record are in it — entered by hand, for the
few clients the firm does this for; nothing here raises a task or a reminder.
Every FY after their first harvest with no entry reads Pending — Missed, once
that FY has closed, because an unused exemption does not carry forward —
unless the client is stopped (``Client.tax_harvest_stopped``). Apart from that
flag nothing about the programme is stored; it is read off the rows.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from ..models import Client, TaxHarvest
from .incentives import fy_start_year
from .tasks import _add_months

# 12.5% LTCG + 4% cess: what a rupee of gain costs once it is over the limit.
TAX_RATE = Decimal("0.13")


def exemption(fy):
    """Sec 112A's tax-free equity LTCG for one FY: ₹1L until FY 2023-24, ₹1.25L
    from FY 2024-25 (Budget, July 2024)."""
    return Decimal(125000) if fy >= 2024 else Decimal(100000)


def fy_label(fy):
    return f"FY {fy}-{(fy + 1) % 100:02d}"


def fy_range(fy):
    return date(fy, 4, 1), date(fy + 1, 3, 31)


def summary(harvests, fy, today):
    """What one client's harvests say about one FY.

    ``harvests`` is that client's rows up to the end of the FY, oldest first.
    """
    start, _ = fy_range(fy)
    this_year = [h for h in harvests if h.date >= start]
    gain = sum((h.gain_booked for h in this_year), Decimal(0))
    limit = exemption(fy)
    last = harvests[-1] if harvests else None
    return {
        "entries": this_year,
        "done": bool(this_year),
        "fy_gain": gain,
        "headroom": max(limit - gain, Decimal(0)),
        "over": max(gain - limit, Decimal(0)),
        # Only the slice inside the limit escapes tax; the rest is taxed anyway.
        "tax_saved": min(gain, limit) * TAX_RATE,
        "total_gain": sum((h.gain_booked for h in harvests), Decimal(0)),
        "last": last,
        "portfolio_value": next((h.portfolio_value for h in reversed(harvests)
                                 if h.portfolio_value), Decimal(0)),
    } | next_harvest(last, today)


def next_harvest(last, today):
    """The next harvest falls a year on: units bought back are long-term once
    held MORE than 12 months — sold sooner, the gain is short-term and taxed at
    20%. So it counts from the repurchase, or the sale while nothing has been
    bought back. ``days`` is negative once it is overdue."""
    if not last:
        return {"next_harvest": None, "days": None, "overdue": 0}
    bought_back = last.reinvestment == TaxHarvest.REINVEST_MF and last.reinvested_on
    due = _add_months(bought_back or last.date, 12) + timedelta(days=1)
    days = (due - today).days
    return {"next_harvest": due, "days": days, "overdue": max(-days, 0)}


def book(fy, today):
    """``(done, pending, stopped)`` for one FY: one summary per client.

    Pending = harvested in an earlier FY, nothing yet in this one, not stopped.
    A stopped client's harvests this FY still count as done — they happened.
    """
    _, end = fy_range(fy)
    # ponytail: every harvest up to the FY's end is read and grouped in Python —
    # one row per client per year, so thousands at most; aggregate in SQL past that.
    by_client = defaultdict(list)
    for h in TaxHarvest.objects.filter(date__lte=end).order_by("date", "id"):
        by_client[h.client_id].append(h)
    clients = Client.objects.select_related("mapped_to__user").in_bulk(list(by_client))
    done, pending, stopped = [], [], []
    for cid, harvests in by_client.items():
        row = summary(harvests, fy, today)
        row["client"] = clients[cid]
        if row["done"]:
            done.append(row)
        if row["client"].tax_harvest_stopped:
            stopped.append(row)
        elif not row["done"]:
            pending.append(row)
    done.sort(key=lambda r: r["entries"][-1].date, reverse=True)
    pending.sort(key=lambda r: r["next_harvest"])      # soonest (or most overdue) first
    stopped.sort(key=lambda r: r["client"].name)
    return done, pending, stopped


def client_record(client, today):
    """The client profile's Tax Harvest tab: this FY's standing + every FY."""
    harvests = list(client.tax_harvests.select_related("created_by").order_by("date", "id"))
    fy = fy_start_year(today)
    years = defaultdict(list)
    for h in harvests:
        years[h.fy].append(h)
    by_year = []
    for y, rows in sorted(years.items(), reverse=True):
        gain = sum(h.gain_booked for h in rows)
        by_year.append({"label": fy_label(y), "entries": list(reversed(rows)), "gain": gain,
                        "limit": exemption(y), "over": max(gain - exemption(y), 0)})
    return {
        "fy": fy, "fy_label": fy_label(fy), "limit": exemption(fy),
        "now": summary(harvests, fy, today),
        "stopped": client.tax_harvest_stopped,
        # In the programme, nothing booked this FY yet.
        "pending": (bool(harvests) and harvests[-1].date < fy_range(fy)[0]
                    and not client.tax_harvest_stopped),
        "years": by_year,
        "tax_saved": sum((min(y["gain"], y["limit"]) for y in by_year), Decimal(0)) * TAX_RATE,
    }


def awaiting():
    """Every harvest still at 50% — sold, money not yet back in — oldest first,
    whatever its FY: until it is repurchased or converted it is AUM lost."""
    return (TaxHarvest.objects.filter(reinvestment="", gain_booked__gt=0)
            .select_related("client").order_by("date", "id"))

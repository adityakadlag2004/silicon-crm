"""The Business Report sheet and the month's celebration built on it.

`sheet()` is the grid the web report and the app's Daily Report both print.
`celebration()` reads its winners off that same grid, so the celebration can
never disagree with the table above it; the certificates page and the
`celebration_announce` cron read `awards()` off the celebration in turn.
"""
from calendar import month_name
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum

from ..models import Client, Employee, EmployeeTarget, MonthlyTargetHistory, Product, Sale
from ..views.helpers import category_name_map


def sheet(sel_date=None, sel_year=None, sel_month=None):
    """The Business Report grid, for one day (`sel_date`) or one month.

    One row per active employee, one column per reportable main product, plus
    accounts opened and points; grand totals alongside. Shared by the web page
    and the app's Daily Report — the roll-up rules below are fiddly enough that
    a second copy of them would drift.
    """
    daily = sel_date is not None
    if daily:
        approved = Sale.objects.filter(status="approved", date=sel_date)
    else:
        approved = Sale.objects.filter(status="approved", date__year=sel_year, date__month=sel_month)

    # Top-level products only. The life plan catalogue alone is ~40 sub-products,
    # and a column per plan makes this sheet unreadable — a sub-product's sales
    # roll up into its category (Term Plan -> Life Insurance).
    cat_map = category_name_map()
    products = list(
        Product.objects.filter(is_active=True, parent__isnull=True, show_in_reports=True)
        .order_by("display_order", "name")
        .values_list("name", flat=True)
    )
    # Products switched off for this sheet (Product Management). Their sales are
    # untouched — the column just isn't printed, including for a retired
    # category that would otherwise be re-added below because it has business.
    hidden = set(
        Product.objects.filter(parent__isnull=True, show_in_reports=False)
        .values_list("name", flat=True)
    )

    used_products = set(
        approved.exclude(product="")
        .values_list("product", flat=True)
        .distinct()
    )
    used_ref_products = set(
        approved.filter(product_ref__isnull=False)
        .values_list("product_ref__name", flat=True)
        .distinct()
    )

    # A retired category with sales this month still needs its column; a
    # sub-product never does, because cat_map folds it into its parent.
    for product_name in sorted(used_products | used_ref_products):
        category = cat_map.get(product_name, product_name)
        if category and category not in products and category not in hidden:
            products.append(category)
    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__first_name")

    # Pre-aggregate amounts grouped by (employee, product) and points by employee.
    # Replaces an N×M loop of per-cell `.aggregate(Sum)` calls with two queries.
    amount_by_emp_product = {}
    for r in approved.values("employee_id", "product").annotate(total=Sum("amount")):
        key = (r["employee_id"], cat_map.get(r["product"], r["product"]))
        amount_by_emp_product[key] = (amount_by_emp_product.get(key, Decimal("0"))
                                      + (r["total"] or Decimal("0")))
    points_by_emp = {
        r["employee_id"]: r["total"] or Decimal("0")
        for r in approved.values("employee_id").annotate(total=Sum("points"))
    }
    # Accounts opened in the window. Client has no created_by, so the credit
    # goes to whoever the client is mapped to — which is also the figure the
    # firm reads this row for ("whose book grew").
    # ponytail: re-mapping a client moves its credit to the new employee; add
    # Client.created_by if that ever matters.
    new_clients = Client.objects.filter(mapped_to__isnull=False)
    if daily:
        new_clients = new_clients.filter(created_at__date=sel_date)
    else:
        new_clients = new_clients.filter(created_at__year=sel_year, created_at__month=sel_month)
    accounts_by_emp = {
        r["mapped_to"]: r["total"]
        for r in new_clients.values("mapped_to").order_by().annotate(total=Count("id"))
    }

    rows = []
    grand = {p: Decimal("0") for p in products}
    grand["points"] = Decimal("0")
    grand_accounts = 0

    for e in employees:
        product_vals = []
        for p in products:
            total = amount_by_emp_product.get((e.id, p), Decimal("0"))
            product_vals.append(total)
            grand[p] += total
        pts = points_by_emp.get(e.id, Decimal("0"))
        grand["points"] += pts
        accounts = accounts_by_emp.get(e.id, 0)
        grand_accounts += accounts
        rows.append({"employee": e, "product_vals": product_vals,
                     "points": pts, "accounts": accounts})

    return {
        "products": products,
        "rows": rows,
        "grand_vals": [grand[p] for p in products],
        "grand_points": grand["points"],
        "grand_accounts": grand_accounts,
    }


# ── Celebration ──────────────────────────────────────────────────────────────

STREAK_LOOKBACK = 12  # months; a longer run still reads "12 months running"


def _prev_month(year, month):
    return (year, month - 1) if month > 1 else (year - 1, 12)


def _name(emp):
    return emp.user.get_full_name() or emp.user.username


def _leaders(rows, value):
    """The highest positive value(row) and everyone tied on it; None if nobody scored."""
    top = max((value(r) for r in rows), default=0)
    if top <= 0:
        return None
    return {"amount": top, "names": [_name(r["employee"]) for r in rows if value(r) == top]}


def _product_leaders(month_sheet):
    """{product: leaders} for every column somebody sold in."""
    out = {}
    for i, p in enumerate(month_sheet["products"]):
        lead = _leaders(month_sheet["rows"], lambda r: r["product_vals"][i])
        if lead:
            out[p] = lead
    return out


def _pct_change(cur, prev):
    return round((cur - prev) * 100 / prev) if prev else None


def month_targets(year, month):
    """{(employee_id, product): monthly target} in force for year/month.

    A closed month reads what `close_month` recorded, so a target changed since
    doesn't rewrite who hit it. The open month has no history yet and reads the
    live targets from the Targets page; an older month with no history had no
    targets on record, and nobody is credited against today's.
    """
    hist = {
        (h.employee_id, h.product): h.target_value
        for h in MonthlyTargetHistory.objects.filter(year=year, month=month, target_value__gt=0)
    }
    today = date.today()
    if hist or (year, month) != (today.year, today.month):
        return hist
    return {(t.employee_id, t.product): t.target_value
            for t in EmployeeTarget.objects.filter(target_value__gt=0)}


def target_champions(month_sheet, targets):
    """Everyone who hit at least one monthly target, all-targets people first.

    Achieved = the sheet's approved, category-rolled business — the same figure
    the Targets page measures progress with.
    """
    col = {p: i for i, p in enumerate(month_sheet["products"])}
    out = []
    for r in month_sheet["rows"]:
        eid = r["employee"].pk
        mine = {p: t for (e, p), t in targets.items() if e == eid}
        hits = [{"product": p, "pct": round(r["product_vals"][col[p]] * 100 / t)}
                for p, t in mine.items() if p in col and r["product_vals"][col[p]] >= t]
        if hits:
            out.append({"name": _name(r["employee"]), "hits": hits,
                        "all": len(hits) == len(mine)})
    out.sort(key=lambda c: (not c["all"], -len(c["hits"]), c["name"]))
    return out


def celebration(year, month, month_sheet=None):
    """The month's winners, all read off the Business Report sheet.

    Top performer per product (with how many months running they've held it),
    Star of the Month by points, most accounts opened, most improved on points,
    target champions, and the team against the month before.
    """
    month_sheet = month_sheet or sheet(sel_year=year, sel_month=month)
    products = []
    for p, lead in _product_leaders(month_sheet).items():
        lead["product"] = p
        lead["streaks"] = {n: 1 for n in lead["names"]}
        products.append(lead)

    # Walk back a month at a time while any current winner is still on a run.
    # ponytail: one sheet (~7 queries) per month walked, at most STREAK_LOOKBACK;
    # a single grouped query over the window if this page ever feels slow.
    py, pm = _prev_month(year, month)
    prev = sheet(sel_year=py, sel_month=pm)
    live = {(w["product"], n) for w in products for n in w["names"]}
    back, step = prev, 1
    while live and step < STREAK_LOOKBACK:
        tops = _product_leaders(back)
        live = {(p, n) for p, n in live if n in tops.get(p, {}).get("names", ())}
        for w in products:
            for n in w["names"]:
                if (w["product"], n) in live:
                    w["streaks"][n] += 1
        step += 1
        py, pm = _prev_month(py, pm)
        if live:
            back = sheet(sel_year=py, sel_month=pm)

    prev_points = {r["employee"].pk: r["points"] for r in prev["rows"]}
    return {
        "products": products,
        "star": _leaders(month_sheet["rows"], lambda r: r["points"]),
        "accounts": _leaders(month_sheet["rows"], lambda r: r["accounts"]),
        "improved": _leaders(month_sheet["rows"],
                             lambda r: r["points"] - prev_points.get(r["employee"].pk, 0)),
        "targets": target_champions(month_sheet, month_targets(year, month)),
        "points_change": _pct_change(month_sheet["grand_points"], prev["grand_points"]),
        "accounts_change": _pct_change(month_sheet["grand_accounts"], prev["grand_accounts"]),
        "prev_month_name": month_name[_prev_month(year, month)[1]],
    }


def awards(cel):
    """One {title, name, detail} per person per award — a certificate each, and
    the lines of the month-end announcement."""
    out = []
    for key, title, unit in (("star", "Star of the Month", "points"),
                             ("accounts", "Most Accounts Opened", "new clients"),
                             ("improved", "Most Improved", "more points than last month")):
        lead = cel[key]
        for n in (lead["names"] if lead else ()):
            out.append({"title": title, "name": n, "detail": f"{lead['amount']:,.0f} {unit}"})
    for w in cel["products"]:
        for n in w["names"]:
            run = w["streaks"][n]
            detail = f"₹{w['amount']:,.0f} business"
            if run > 1:
                detail += f" · {run} months running"
            out.append({"title": f"Top in {w['product']}", "name": n, "detail": detail})
    for c in cel["targets"]:
        detail = ("Hit every target: " if c["all"] else "Hit target: ") + ", ".join(
            f"{h['product']} {h['pct']}%" for h in c["hits"])
        out.append({"title": "Target Champion", "name": c["name"], "detail": detail})
    return out


def announcement(year, month):
    """(title, body) for the month-end celebration push, or None if nobody won."""
    lines = [f"{a['title']}: {a['name']} ({a['detail']})"
             for a in awards(celebration(year, month))]
    if not lines:
        return None
    return f"🎉 {month_name[month]} {year} celebration", "\n".join(lines)


def announce(year, month, *, dry_run=False):
    """Push the month's winners to every active employee's phone and inbox.

    Returns how many people it reached. Idempotent: anyone who already holds the
    notification is skipped, so a re-run never sends it twice.
    """
    from ..models import Notification
    from .tasks import create_notification

    msg = announcement(year, month)
    if not msg:
        return 0
    title, body = msg
    sent = 0
    for emp in Employee.objects.filter(active=True).select_related("user"):
        if Notification.objects.filter(recipient=emp.user, title=title).exists():
            continue
        if not dry_run:
            create_notification(emp.user, title, body, link="", event=None)
        sent += 1
    return sent

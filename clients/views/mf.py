"""Mutual Funds (RTA feeds) — admin screens for the CAMS/KFintech mailback
pipeline: import dashboard, ARN account management, folio↔client linking and
the imported transaction ledger. Business logic lives in services/rta_feed.py.
"""
import io
import re
import zipfile

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..models import (
    ArnAccount,
    Client,
    MutualFundFolio,
    MutualFundTransaction,
    RTAFeedImport,
    RTA_CHOICES,
)
from ..templatetags.custom_filters import inr
from ..permissions import admin_required as _admin_required
from ..services import rta_feed
from .helpers import name_words_q


@_admin_required
def mf_dashboard(request):
    folios = MutualFundFolio.objects.all()
    stats = {
        "folios": folios.count(),
        "folios_linked": folios.filter(client__isnull=False).count(),
        "folios_unlinked": folios.filter(client__isnull=True).count(),
        "transactions": MutualFundTransaction.objects.count(),
    }
    per_arn = list(
        MutualFundTransaction.objects.values("arn__label")
        .annotate(txns=Count("id"), gross=Sum("amount"))
        .order_by("arn__label")
    )
    total_gross = sum((row["gross"] or 0) for row in per_arn)
    folios_url = reverse("clients:mf_folios")
    return render(request, "mf/dashboard.html", {
        "page_title": "Mutual Funds",
        "kpis": [
            {"label": "Folios", "value": inr(stats["folios"]), "color": "#4338CA",
             "url": folios_url},
            {"label": "Linked to clients", "value": inr(stats["folios_linked"]),
             "color": "#15803D", "url": f"{folios_url}?linked=yes"},
            {"label": "Unlinked", "value": inr(stats["folios_unlinked"]),
             "color": "#B45309", "url": f"{folios_url}?linked=no"},
            {"label": "Transactions", "value": inr(stats["transactions"]),
             "color": "#0F766E", "url": reverse("clients:mf_transactions")},
            {"label": "Gross Flows", "value": f"₹{inr(total_gross)}",
             "color": "#0369A1"},
        ],
        "per_arn": per_arn,
        "arn_accounts": ArnAccount.objects.all(),
        "imports": RTAFeedImport.objects.all()[:30],
        "last_import": RTAFeedImport.objects.filter(status=RTAFeedImport.STATUS_PROCESSED).first(),
    })


@_admin_required
@require_POST
def mf_upload(request):
    files = request.FILES.getlist("feed_files")
    if not files:
        messages.error(request, "Choose at least one mailback file (.zip / .dbf / .csv).")
        return redirect("clients:mf_dashboard")
    for f in files:
        if not f.name.lower().endswith(rta_feed.CONTAINER_EXTENSIONS):
            messages.error(request, f"{f.name}: unsupported type — upload .zip, .dbf, .csv or .txt.")
            continue
        feed_import = rta_feed.import_feed_container(
            f.name, f.read(), source=RTAFeedImport.SOURCE_UPLOAD, user=request.user,
        )
        if feed_import.status == RTAFeedImport.STATUS_PROCESSED:
            messages.success(
                request,
                f"{f.name}: {feed_import.rows_imported} rows imported, "
                f"{feed_import.rows_duplicate} duplicates, {feed_import.folios_created} new folios, "
                f"{feed_import.clients_linked} clients auto-linked.",
            )
        elif feed_import.status == RTAFeedImport.STATUS_SKIPPED:
            messages.info(request, f"{f.name}: already imported earlier — skipped.")
        else:
            messages.error(request, f"{f.name}: import failed — see the import log below.")
    return redirect("clients:mf_dashboard")


@_admin_required
@require_POST
def mf_fetch_now(request):
    imports = rta_feed.fetch_from_mailbox()
    if imports is None or not imports:
        messages.info(request, "No new feed emails found (or the feeds mailbox is not configured).")
    else:
        done = sum(1 for i in imports if i.status == RTAFeedImport.STATUS_PROCESSED)
        messages.success(request, f"Fetched {len(imports)} file(s) from the mailbox; {done} processed.")
    return redirect("clients:mf_dashboard")


@_admin_required
@require_POST
def mf_relink(request):
    linked = rta_feed.relink_folios()
    messages.success(request, f"Auto-link re-run: {linked} folio(s) newly linked by PAN.")
    return redirect("clients:mf_dashboard")


@_admin_required
@require_POST
def mf_arn_save(request):
    account_id = request.POST.get("account_id")
    label = (request.POST.get("label") or "").strip()
    arn_code = (request.POST.get("arn_code") or "").strip()
    sub_broker_code = (request.POST.get("sub_broker_code") or "").strip()
    if not label or not arn_code:
        messages.error(request, "Label and ARN code are both required.")
        return redirect("clients:mf_dashboard")
    if account_id:
        account = get_object_or_404(ArnAccount, id=account_id)
        account.label, account.arn_code, account.sub_broker_code = label, arn_code, sub_broker_code
        account.is_active = request.POST.get("is_active") == "on"
        account.save()
        messages.success(request, f"Updated ARN account '{label}'.")
    else:
        if ArnAccount.objects.filter(label=label).exists():
            messages.error(request, f"An ARN account named '{label}' already exists.")
        else:
            ArnAccount.objects.create(label=label, arn_code=arn_code, sub_broker_code=sub_broker_code)
            messages.success(request, f"Added ARN account '{label}'.")
    return redirect("clients:mf_dashboard")


@_admin_required
@require_POST
def mf_arn_delete(request, account_id):
    account = get_object_or_404(ArnAccount, id=account_id)
    if account.folios.exists() or account.mf_transactions.exists():
        account.is_active = False
        account.save(update_fields=["is_active"])
        messages.info(request, f"'{account.label}' has imported data, so it was deactivated instead of deleted.")
    else:
        account.delete()
        messages.success(request, f"Deleted ARN account '{account.label}'.")
    return redirect("clients:mf_dashboard")


@_admin_required
def mf_folios(request):
    qs = (
        MutualFundFolio.objects.select_related("client", "arn")
        .annotate(txn_count=Count("transactions"))
        .order_by("investor_name", "folio_number")  # annotate's GROUP BY drops Meta.ordering
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(folio_number__icontains=q) | name_words_q("investor_name", q)
            | Q(pan__icontains=q) | Q(amc_name__icontains=q) | name_words_q("client__name", q)
        )
    linked = request.GET.get("linked")
    if linked == "yes":
        qs = qs.filter(client__isnull=False)
    elif linked == "no":
        qs = qs.filter(client__isnull=True)
    arn_id = request.GET.get("arn")
    if arn_id and arn_id.isdigit():
        qs = qs.filter(arn_id=int(arn_id))

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    # Name suggestion beside each unlinked row — the feed often omits PAN, so
    # PAN auto-linking can't help and someone has to say who this is.
    page.object_list = list(page.object_list)
    suggestions = rta_feed.suggest_clients_for_folios(page.object_list)
    for folio in page.object_list:
        folio.suggestion = suggestions.get(folio.id)

    all_folios = MutualFundFolio.objects.all()
    base_url = reverse("clients:mf_folios")
    return render(request, "mf/folios.html", {
        "page_title": "MF Folios",
        "kpis": [
            {"label": "All Folios", "value": all_folios.count(), "color": "#4338CA",
             "url": base_url, "active": not linked},
            {"label": "Linked to a Client",
             "value": all_folios.filter(client__isnull=False).count(), "color": "#15803D",
             "url": f"{base_url}?linked=yes", "active": linked == "yes"},
            {"label": "Unlinked",
             "value": all_folios.filter(client__isnull=True).count(), "color": "#BE123C",
             "url": f"{base_url}?linked=no", "active": linked == "no"},
        ],
        "page": page,
        "q": q,
        "linked": linked or "",
        "arn_id": arn_id or "",
        "arn_accounts": ArnAccount.objects.all(),
    })


@_admin_required
def mf_folio_batches(request):
    """Download our folios as RTA request files — one folio per line, 500 per
    file, zipped. Attach the file to the CAMS/KFintech by-folio mailback
    request to pull back history for folios the daily feed missed."""
    size = (request.GET.get("size") or "").strip()
    size = int(size) if size.isdigit() and 0 < int(size) <= 5000 else rta_feed.FOLIO_BATCH_SIZE
    files = rta_feed.folio_request_batches(size=size, rta=request.GET.get("rta") or "")
    if not files:
        messages.error(request, "No folios to export yet.")
        return redirect("clients:mf_folios")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files:
            zf.writestr(name, text)
    response = HttpResponse(buf.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="folio_batches_{timezone.localdate()}.zip"')
    return response


@_admin_required
@require_POST
def mf_folio_auto_link(request):
    """Link every unlinked folio whose investor name matches exactly one
    client. Weak and ambiguous matches are left for a human."""
    linked, ambiguous = rta_feed.auto_link_by_name()
    if linked:
        messages.success(request, f"{linked} folio(s) auto-linked by name.")
    else:
        messages.info(request, "Nothing to auto-link — no unambiguous name matches left.")
    if ambiguous:
        messages.warning(
            request,
            f"{ambiguous} folio(s) skipped: the name matches more than one client. "
            f"Link those from the row buttons so the right person is picked.",
        )
    return redirect(_safe_next(request))


@_admin_required
def mf_folio_link(request, folio_id):
    folio = get_object_or_404(MutualFundFolio.objects.select_related("client"), id=folio_id)

    if request.method == "POST":
        from ..models import SipRegistration

        if request.POST.get("action") == "unlink":
            old_client_id = folio.client_id
            folio.client = None
            folio.save(update_fields=["client", "updated_at"])
            # the folio's SIP registrations follow it out
            SipRegistration.objects.filter(
                folio=folio, client_id=old_client_id).update(client=None)
            rta_feed.refresh_client_sip_fields(client_ids=[old_client_id])
            messages.success(request, f"Folio {folio.folio_number} unlinked.")
            return redirect(_safe_next(request))
        client_id = request.POST.get("client_id")
        if client_id and client_id.isdigit():
            client = get_object_or_404(Client, id=int(client_id))
            folio.client = client
            folio.save(update_fields=["client", "updated_at"])
            SipRegistration.objects.filter(
                folio_number=folio.folio_number, client__isnull=True).update(client=client)
            rta_feed.refresh_client_sip_fields(client_ids=[client.id])
            messages.success(request, f"Folio {folio.folio_number} linked to {client.name}.")
            return redirect(_safe_next(request))
        messages.error(request, "Pick a client to link.")

    q = (request.GET.get("q") or "").strip()
    candidates = Client.objects.all()
    if q:
        candidates = candidates.filter(name_words_q("name", q) | Q(pan__icontains=q) | Q(phone__icontains=q))
    elif folio.investor_name:
        first_word = folio.investor_name.split()[0]
        candidates = candidates.filter(name__icontains=first_word)
    else:
        candidates = candidates.none()

    return render(request, "mf/folio_link.html", {
        "page_title": "Link Folio",
        "folio": folio,
        "q": q,
        "candidates": candidates.order_by("name")[:30],
    })


@_admin_required
@require_POST
def mf_folio_create_client(request, folio_id):
    """Create a Client straight from an unlinked folio's RTA data — the
    investor exists at the RTA but not in the CRM. Name and PAN come from
    the folio; everything sharing that PAN links immediately."""
    folio = get_object_or_404(MutualFundFolio, id=folio_id)
    next_url = _safe_next(request)
    if folio.client_id:
        messages.info(request, f"Folio {folio.folio_number} is already linked.")
        return redirect(next_url)

    pan = re.sub(r"[^A-Z0-9]", "", (folio.pan or "").upper())
    if pan:
        existing = Client.objects.filter(pan__iexact=pan).first()
        if existing:
            folio.client = existing
            folio.save(update_fields=["client", "updated_at"])
            linked = rta_feed.relink_folios()
            messages.info(request, f"'{existing.name}' already has PAN {pan} — "
                                   f"linked this folio to them ({linked + 1} record(s)).")
            return redirect(next_url)

    name = (folio.investor_name or "").strip().title() or f"Folio {folio.folio_number}"
    client = Client.objects.create(name=name, pan=pan)
    folio.client = client
    folio.save(update_fields=["client", "updated_at"])
    linked = rta_feed.relink_folios() + 1  # same-PAN folios + SIP registrations
    messages.success(
        request,
        f"Client '{name}' created from folio {folio.folio_number}"
        f"{f' (PAN {pan})' if pan else ' (no PAN in feed)'} — {linked} record(s) linked. "
        f"Add phone/email on their profile when known.",
    )
    return redirect(next_url)


@_admin_required
def mf_transactions(request):
    qs = MutualFundTransaction.objects.select_related("folio__client", "arn")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(folio__folio_number__icontains=q) | name_words_q("folio__investor_name", q)
            | Q(scheme_name__icontains=q) | Q(txn_type__icontains=q)
            | name_words_q("folio__client__name", q)
        )
    arn_id = request.GET.get("arn")
    if arn_id and arn_id.isdigit():
        qs = qs.filter(arn_id=int(arn_id))
    rta = request.GET.get("rta")
    if rta in dict(RTA_CHOICES):
        qs = qs.filter(rta=rta)

    totals = qs.aggregate(gross=Sum("amount"))
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "mf/transactions.html", {
        "page_title": "MF Transactions",
        "kpis": [
            {"label": "Transactions", "value": page.paginator.count, "color": "#4338CA"},
            {"label": "Gross Value", "value": f"\u20b9{inr(totals['gross'] or 0)}",
             "color": "#15803D"},
        ],
        "page": page,
        "q": q,
        "arn_id": arn_id or "",
        "rta": rta or "",
        "arn_accounts": ArnAccount.objects.all(),
        "rta_choices": RTA_CHOICES,
    })


_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _safe_next(request):
    """The posted `next` when it points back into this site, else the folios
    list. Never hand a caller-supplied URL straight to redirect()."""
    nxt = request.POST.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(
            nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return reverse("clients:mf_folios")


@_admin_required
def mf_folio_match(request):
    """Match Folios screen: name-based suggestions pairing unlinked folios
    with clients. Per row: adopt the folio's PAN onto the client (and link
    everything with that PAN), or link the folios without touching PAN."""
    from ..models import SipRegistration

    if request.method == "POST":
        action = request.POST.get("action")
        client = get_object_or_404(Client, id=request.POST.get("client_id"))
        folio_ids = [int(v) for v in request.POST.getlist("folio_ids") if v.isdigit()]
        folios = list(MutualFundFolio.objects.filter(id__in=folio_ids, client__isnull=True))

        if action == "adopt":
            pan = (request.POST.get("pan") or "").strip().upper()
            if not _PAN_RE.match(pan):
                messages.error(request, f"'{pan}' is not a valid PAN.")
                return redirect("clients:mf_folio_match")
            if client.pan and client.pan.strip().upper() != pan:
                messages.error(
                    request,
                    f"{client.name} already has PAN {client.pan} — not overwriting with {pan}. "
                    f"Use 'Link only' if these folios really belong to them.",
                )
                return redirect("clients:mf_folio_match")
            if not client.pan:
                client.pan = pan
                client.save(update_fields=["pan"])
            linked = rta_feed.relink_folios()
            for folio in folios:  # folios without a PAN in the group, if any
                if folio.client_id is None:
                    folio.client = client
                    folio.save(update_fields=["client", "updated_at"])
                    linked += 1
            messages.success(request, f"{client.name}: PAN saved, {linked} record(s) linked.")
        elif action == "link":
            for folio in folios:
                folio.client = client
                folio.save(update_fields=["client", "updated_at"])
            SipRegistration.objects.filter(
                folio_number__in=[f.folio_number for f in folios], client__isnull=True,
            ).update(client=client)
            messages.success(request, f"{len(folios)} folio(s) linked to {client.name}.")
        return redirect("clients:mf_folio_match")

    suggestions = rta_feed.suggest_folio_matches()
    q = (request.GET.get("q") or "").strip().lower()
    if q:
        suggestions = [
            s for s in suggestions
            if q in s["investor_name"].lower() or q in s["client"].name.lower()
        ]
    unlinked_total = MutualFundFolio.objects.filter(client__isnull=True).count()
    return render(request, "mf/folio_match.html", {
        "page_title": "Match Folios",
        "suggestions": suggestions[:300],
        "suggestion_count": len(suggestions),
        "unlinked_total": unlinked_total,
        "q": q,
    })


@_admin_required
def mf_cob(request):
    """COB Opportunities: our clients' SIP streams running under other
    brokers' codes — file a Change of Broker to bring the trail here."""
    groups = rta_feed.cob_opportunities()

    live = [g for g in groups if g["live"]]
    stopped = [g for g in groups if not g["live"]]
    tiles = {
        "live_n": len(live),
        "live_monthly": sum(g["monthly"] for g in live),
        "clients": len({g["client"].id for g in groups if g["client"]}),
        "unlinked": sum(1 for g in groups if not g["client"]),
        "total_flow": sum(g["total"] for g in groups),
    }
    return render(request, "mf/cob.html", {
        "page_title": "COB Opportunities",
        "live": live,
        "stopped": stopped,
        "tiles": tiles,
        "by_client": rta_feed.outside_flows_by_client(),
    })


def _sip_leak_qs():
    """Ceased registrations that are real terminations — the AUM leaks.
    Natural expiries and process rejections are not leaks."""
    from ..models import SipRegistration

    return SipRegistration.objects.filter(status=SipRegistration.STATUS_CEASED).exclude(
        Q(rta_status__icontains="expire") | Q(rta_status__icontains="reject"))


@_admin_required
def mf_sips_month(request, year, month):
    """Month drill-down from the SIP flow table: every registration started,
    terminated, or naturally expired in that month."""
    from datetime import date as date_cls, timedelta

    from django.http import Http404

    from ..models import SipRegistration

    try:
        m_start = date_cls(year, month, 1)
    except ValueError:
        raise Http404("No such month")
    m_end = (m_start + timedelta(days=32)).replace(day=1)

    base = SipRegistration.objects.select_related("client")
    new_regs = list(base.filter(registered_on__gte=m_start,
                                registered_on__lt=m_end).order_by("-amount"))
    stopped_regs = list(_sip_leak_qs().select_related("client")
                        .filter(ceased_on__gte=m_start, ceased_on__lt=m_end)
                        .order_by("-amount"))
    expired_regs = list(base.filter(status=SipRegistration.STATUS_CEASED,
                                    rta_status__icontains="expire",
                                    end_date__gte=m_start, end_date__lt=m_end)
                        .order_by("-amount"))

    new_total = sum((r.amount or 0) for r in new_regs)
    stopped_total = sum((r.amount or 0) for r in stopped_regs)
    net = new_total - stopped_total
    return render(request, "mf/sips_month.html", {
        "page_title": f"SIP flow — {m_start:%B %Y}",
        "m_start": m_start,
        "new_regs": new_regs,
        "stopped_regs": stopped_regs,
        "expired_regs": expired_regs,
        "new_total": new_total,
        "stopped_total": stopped_total,
        "net": net,
        "net_abs": abs(net),
    })


@_admin_required
def mf_sips(request):
    """SIP register dashboard: the firm's systematic book at a glance —
    headline tiles, monthly starts-vs-stops flow, leak watch (real
    terminations + at-risk plans), scheme concentration, and the register
    itself with tabs."""
    from datetime import timedelta

    from django.db.models import Count
    from django.utils import timezone

    from ..models import MutualFundTransaction, SipRegistration

    today = timezone.localdate()
    month_start = today.replace(day=1)
    month_ago = today - timedelta(days=30)
    risk_cutoff = today - timedelta(days=45)

    base = SipRegistration.objects.all()
    active_qs = base.filter(status=SipRegistration.STATUS_ACTIVE)
    leak_qs = _sip_leak_qs()

    # folios that received a systematic installment recently — anything
    # active and older than the cutoff without one is "at risk"
    recent_sip_folios = set(
        MutualFundTransaction.objects.filter(trade_date__gte=risk_cutoff)
        .filter(
            Q(txn_type__icontains="sip") | Q(txn_type__icontains="systematic")
            | Q(txn_type__iexact="sin")
        )
        .values_list("folio__folio_number", flat=True)
    )
    at_risk_regs = [
        r for r in active_qs.filter(start_date__lt=risk_cutoff)
        if r.folio_number not in recent_sip_folios
    ]
    at_risk_ids = [r.id for r in at_risk_regs]

    def _bundle(qs):
        agg = qs.aggregate(n=Count("id"), total=Sum("amount"))
        return {"n": agg["n"] or 0, "total": agg["total"] or 0}

    tiles = {
        "active": _bundle(active_qs),
        "new_month": _bundle(base.filter(Q(registered_on__gte=month_start)
                                         | Q(registered_on__isnull=True,
                                             first_seen_at__date__gte=month_start))),
        "stopped_month": _bundle(leak_qs.filter(ceased_on__gte=month_start)),
        "at_risk": {"n": len(at_risk_regs),
                    "total": sum((r.amount or 0) for r in at_risk_regs)},
        "expired": _bundle(base.filter(status=SipRegistration.STATUS_CEASED,
                                       rta_status__icontains="expire")),
    }
    tiles["net_month"] = tiles["new_month"]["total"] - tiles["stopped_month"]["total"]
    tiles["net_month_abs"] = abs(tiles["net_month"])

    # monthly flow, last 6 months: new by registration date, stopped by
    # (real) terminate date
    months = []
    cursor = month_start
    for _ in range(6):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    flow = []
    for m_start in months:
        m_end = (m_start + timedelta(days=32)).replace(day=1)
        new = _bundle(base.filter(registered_on__gte=m_start, registered_on__lt=m_end))
        stopped = _bundle(leak_qs.filter(ceased_on__gte=m_start, ceased_on__lt=m_end))
        net_val = (new["total"] or 0) - (stopped["total"] or 0)
        flow.append({
            "month": m_start, "new": new, "stopped": stopped,
            "net": net_val, "net_abs": abs(net_val),
        })
    flow_max = max([f["new"]["total"] for f in flow]
                   + [f["stopped"]["total"] for f in flow] + [1])
    for f in flow:
        f["new_pct"] = round(100 * (f["new"]["total"] or 0) / flow_max)
        f["stopped_pct"] = round(100 * (f["stopped"]["total"] or 0) / flow_max)

    # leak watch lists
    recent_stopped = list(
        leak_qs.select_related("client").order_by("-ceased_on", "-updated_at")[:15])
    at_risk_top = sorted(at_risk_regs, key=lambda r: r.amount or 0, reverse=True)[:15]

    # concentration: active book by scheme
    scheme_rows = list(
        active_qs.exclude(scheme_name="").values("scheme_name")
        .annotate(n=Count("id"), total=Sum("amount")).order_by("-total")[:10])
    scheme_max = max([s["total"] or 0 for s in scheme_rows] + [1])
    for s in scheme_rows:
        s["pct"] = round(100 * (s["total"] or 0) / scheme_max)

    tab = request.GET.get("tab", "active")
    qs = base.select_related("client", "folio", "arn")
    if tab == "new":
        qs = qs.filter(Q(registered_on__gte=month_ago)
                       | Q(registered_on__isnull=True, first_seen_at__date__gte=month_ago))
    elif tab == "ceased":
        qs = qs.filter(status=SipRegistration.STATUS_CEASED).order_by("-ceased_on")
    elif tab == "at_risk":
        qs = qs.filter(id__in=at_risk_ids).order_by("-amount")
    elif tab == "all":
        pass
    else:
        tab = "active"
        qs = qs.filter(status=SipRegistration.STATUS_ACTIVE)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(folio_number__icontains=q) | name_words_q("investor_name", q)
            | Q(scheme_name__icontains=q) | name_words_q("client__name", q)
            | Q(pan__icontains=q)
        )

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "mf/sips.html", {
        "page_title": "SIP Register",
        "page": page,
        "tab": tab,
        "q": q,
        "tiles": tiles,
        "flow": flow,
        "recent_stopped": recent_stopped,
        "at_risk_top": at_risk_top,
        "scheme_rows": scheme_rows,
        "today": today,
        "at_risk_ids": set(at_risk_ids),
    })

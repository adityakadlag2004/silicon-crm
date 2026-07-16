"""Mutual Funds (RTA feeds) — admin screens for the CAMS/KFintech mailback
pipeline: import dashboard, ARN account management, folio↔client linking and
the imported transaction ledger. Business logic lives in services/rta_feed.py.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import (
    ArnAccount,
    Client,
    MutualFundFolio,
    MutualFundTransaction,
    RTAFeedImport,
    RTA_CHOICES,
)
from ..permissions import admin_required as _admin_required
from ..services import rta_feed


@_admin_required
def mf_dashboard(request):
    folios = MutualFundFolio.objects.all()
    stats = {
        "folios": folios.count(),
        "folios_linked": folios.filter(client__isnull=False).count(),
        "folios_unlinked": folios.filter(client__isnull=True).count(),
        "transactions": MutualFundTransaction.objects.count(),
    }
    per_arn = (
        MutualFundTransaction.objects.values("arn__label")
        .annotate(txns=Count("id"), gross=Sum("amount"))
        .order_by("arn__label")
    )
    return render(request, "mf/dashboard.html", {
        "page_title": "Mutual Funds",
        "stats": stats,
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
            Q(folio_number__icontains=q) | Q(investor_name__icontains=q)
            | Q(pan__icontains=q) | Q(amc_name__icontains=q) | Q(client__name__icontains=q)
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
    return render(request, "mf/folios.html", {
        "page_title": "MF Folios",
        "page": page,
        "q": q,
        "linked": linked or "",
        "arn_id": arn_id or "",
        "arn_accounts": ArnAccount.objects.all(),
    })


@_admin_required
def mf_folio_link(request, folio_id):
    folio = get_object_or_404(MutualFundFolio.objects.select_related("client"), id=folio_id)

    if request.method == "POST":
        if request.POST.get("action") == "unlink":
            folio.client = None
            folio.save(update_fields=["client", "updated_at"])
            messages.success(request, f"Folio {folio.folio_number} unlinked.")
            return redirect("clients:mf_folios")
        client_id = request.POST.get("client_id")
        if client_id and client_id.isdigit():
            client = get_object_or_404(Client, id=int(client_id))
            folio.client = client
            folio.save(update_fields=["client", "updated_at"])
            messages.success(request, f"Folio {folio.folio_number} linked to {client.name}.")
            return redirect("clients:mf_folios")
        messages.error(request, "Pick a client to link.")

    q = (request.GET.get("q") or "").strip()
    candidates = Client.objects.all()
    if q:
        candidates = candidates.filter(Q(name__icontains=q) | Q(pan__icontains=q) | Q(phone__icontains=q))
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
def mf_transactions(request):
    qs = MutualFundTransaction.objects.select_related("folio__client", "arn")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(folio__folio_number__icontains=q) | Q(folio__investor_name__icontains=q)
            | Q(scheme_name__icontains=q) | Q(txn_type__icontains=q)
            | Q(folio__client__name__icontains=q)
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
        "page": page,
        "q": q,
        "arn_id": arn_id or "",
        "rta": rta or "",
        "gross": totals["gross"] or 0,
        "arn_accounts": ArnAccount.objects.all(),
        "rta_choices": RTA_CHOICES,
    })

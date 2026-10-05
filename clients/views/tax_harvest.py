"""Tax harvesting register: each client's yearly LTCG harvest, done vs pending."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Min
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..forms import TaxHarvestForm
from ..models import Client, TaxHarvest
from ..permissions import is_admin
from ..services import tax_harvest as th
from ..services.incentives import fy_start_year
from ..templatetags.custom_filters import inr


def _can_delete(user, harvest):
    return is_admin(user) or harvest.created_by_id == user.id


@login_required
def tax_harvest_list(request):
    """One FY at a time: who has booked this year's gain, who is still waiting."""
    today = timezone.localdate()
    current = fy_start_year(today)
    first = TaxHarvest.objects.aggregate(d=Min("date"))["d"]
    years = list(range(current, fy_start_year(first) - 1, -1)) if first else [current]
    fy = request.GET.get("fy", "")
    fy = int(fy) if fy.isdigit() and int(fy) in years else current
    # An unused exemption does not carry forward: once the FY is over it is missed.
    waiting = "Pending" if fy == current else "Missed"
    done, pending, stopped = th.book(fy, today)
    awaiting = list(th.awaiting())
    tab = request.GET.get("tab")
    tab = tab if tab in ("done", "stopped", "awaiting") else "pending"

    base = reverse("clients:tax_harvest_list")
    kpis = [
        {"label": "Done", "value": len(done), "color": "#15803D",
         "url": f"{base}?fy={fy}&tab=done", "active": tab == "done", "sub": th.fy_label(fy)},
        {"label": waiting, "value": len(pending), "color": "#B45309" if waiting == "Pending" else "#BE123C",
         "url": f"{base}?fy={fy}&tab=pending", "active": tab == "pending"},
        {"label": "Profit Booked", "color": "#0369A1",
         "value": f"₹{inr(sum(r['fy_gain'] for r in done))}", "sub": "tax-free gain this FY"},
        {"label": "Tax Saved (est.)", "color": "#7E22CE",
         "value": f"₹{inr(sum(r['tax_saved'] for r in done))}", "sub": "12.5% LTCG + cess"},
    ]
    if awaiting:
        kpis.insert(2, {"label": "Awaiting Repurchase", "value": len(awaiting), "color": "#BE123C",
                        "url": f"{base}?fy={fy}&tab=awaiting", "active": tab == "awaiting",
                        "sub": "sold, not yet back in · 50%"})
    over = sum(1 for r in done if r["over"])
    if over:
        kpis.append({"label": "Over the Limit", "value": over, "color": "#BE123C",
                     "sub": f"booked past ₹{inr(th.exemption(fy))}"})

    q = (request.GET.get("q") or "").strip()
    if q:
        words = q.lower().split()
        done = [r for r in done if all(w in r["client"].name.lower() for w in words)]
        pending = [r for r in pending if all(w in r["client"].name.lower() for w in words)]
        stopped = [r for r in stopped if all(w in r["client"].name.lower() for w in words)]
        awaiting = [h for h in awaiting if all(w in h.client.name.lower() for w in words)]

    return render(request, "clients/tax_harvest_list.html", {
        "crumbs": [{"label": "Clients", "url": reverse("clients:all_clients")},
                   {"label": "Tax Harvesting"}],
        "kpis": kpis,
        "fy": fy, "fy_label": th.fy_label(fy), "limit": th.exemption(fy),
        "years": [{"fy": y, "label": th.fy_label(y)} for y in years],
        "tab": tab, "waiting": waiting, "q": q, "today": today,
        "done": done, "pending": pending, "stopped": stopped, "awaiting": awaiting,
    })


@login_required
def tax_harvest_form(request, harvest_id=None):
    """Record a harvest (``?client=<id>`` pre-picks the client) or correct one."""
    harvest = get_object_or_404(TaxHarvest, pk=harvest_id) if harvest_id else None
    if request.method == "POST":
        form = TaxHarvestForm(request.POST, instance=harvest)
        if form.is_valid():
            obj = form.save(commit=False)
            if harvest is None:
                obj.created_by = request.user
            obj.save()
            messages.success(request, f"Tax harvest {'updated' if harvest else 'recorded'} "
                                      f"for {obj.client.name} — {obj.progress}% done.")
            if not obj.is_complete:
                messages.warning(request, "Only sold so far, so it is out of our AUM. Edit this "
                                          "entry once it is repurchased or converted to insurance.")
            start, end = th.fy_range(obj.fy)
            booked = sum(obj.client.tax_harvests.filter(date__range=(start, end))
                         .values_list("gain_booked", flat=True))
            limit = th.exemption(obj.fy)
            if booked > limit:
                # Not refused: the redemption has already happened, and the record
                # must say what was done. It must also say what it costs.
                messages.warning(request, f"{th.fy_label(obj.fy)} now totals ₹{inr(booked)} — "
                                          f"₹{inr(booked - limit)} over the ₹{inr(limit)} "
                                          f"exemption, and that part is taxable.")
            return redirect(reverse("clients:client_profile", args=[obj.client_id]) + "#tax")
    else:
        form = TaxHarvestForm(instance=harvest, initial=None if harvest else {
            "client": request.GET.get("client"), "date": timezone.localdate()})
    cid = str(form["client"].value() or "")
    return render(request, "clients/tax_harvest_form.html", {
        "crumbs": [{"label": "Clients", "url": reverse("clients:all_clients")},
                   {"label": "Tax Harvesting", "url": reverse("clients:tax_harvest_list")},
                   {"label": "Edit" if harvest else "Record"}],
        "form": form, "harvest": harvest,
        "picked": Client.objects.filter(pk=cid).first() if cid.isdigit() else None,
        "can_delete": harvest is not None and _can_delete(request.user, harvest),
        "limit": th.exemption(fy_start_year(timezone.localdate())),
    })


@login_required
@require_POST
def tax_harvest_delete(request, harvest_id):
    """Admin, or whoever recorded it."""
    harvest = get_object_or_404(TaxHarvest, pk=harvest_id)
    if not _can_delete(request.user, harvest):
        messages.error(request, "Only an admin, or whoever recorded it, can delete a harvest.")
        return redirect("clients:tax_harvest_edit", harvest_id=harvest.pk)
    client_id = harvest.client_id
    harvest.delete()
    messages.success(request, "Tax harvest entry deleted.")
    return redirect(reverse("clients:client_profile", args=[client_id]) + "#tax")


@login_required
@require_POST
def tax_harvest_stop(request, client_id):
    """Stop (or resume) a client's harvesting. Stopped = never pending again;
    the harvests already booked stay on record."""
    client = get_object_or_404(Client, pk=client_id)
    client.tax_harvest_stopped = request.POST.get("stop") == "1"
    client.save(update_fields=["tax_harvest_stopped"])
    messages.success(request, f"Tax harvesting {'stopped' if client.tax_harvest_stopped else 'resumed'} "
                              f"for {client.name}.")
    back = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect(reverse("clients:client_profile", args=[client.pk]) + "#tax")

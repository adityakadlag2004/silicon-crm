"""Client management views: list, add, edit, search, map, reassign, analysis."""
from decimal import Decimal
from urllib.parse import quote

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test, permission_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.db.models import Count, Q, Sum
from django.db import transaction
from django.core.paginator import Paginator
from django.urls import reverse
from django.conf import settings

from .. import permissions
from ..models import Client, Employee, Family, MessageTemplate, Product, Renewal, Sale
from ..forms import ClientForm, ClientReassignForm
from ..services.google_drive import DriveNotConfigured, get_or_create_client_folder
from ..templatetags.custom_filters import inr
from .helpers import parse_date_param, name_words_q


PER_PAGE = getattr(settings, "PER_PAGE", 50)


def _badge_class_for_product(product):
    code = (product.code or "").strip().upper()
    name = (product.name or "").strip().lower()
    if "SIP" in code or "sip" in name:
        return "pb-sip"
    if "PMS" in code or "pms" in name:
        return "pb-pms"
    if "LIFE" in code or "life" in name:
        return "pb-life"
    if "HEALTH" in code or "health" in name:
        return "pb-health"
    if "MOTOR" in code or "motor" in name:
        return "pb-motor"
    return ""


def _build_client_product_filters(request):
    # One filter per main product; a client's sub-product business counts
    # towards its category (see _client_product_totals_map).
    active_products = Product.objects.selectable().main().in_display_order()
    product_filters = []

    for product in active_products:
        prefix = f"product_{product.id}"
        product_filters.append({
            "product": product,
            "status_field": prefix,
            "badge_class": _badge_class_for_product(product),
            "status_param": f"{prefix}_status",
            "min_param": f"{prefix}_min",
            "max_param": f"{prefix}_max",
            "status_value": request.GET.get(f"{prefix}_status", ""),
            "min_value": request.GET.get(f"{prefix}_min", ""),
            "max_value": request.GET.get(f"{prefix}_max", ""),
        })
    return product_filters


def _client_product_totals_map(client_ids, product_ids):
    """{(client, main product): total business}.

    Sub-products fold into their category, so a client whose only life
    business is a Term Plan still answers "yes" to the Life Insurance filter.
    """
    totals_map = {}
    if not client_ids or not product_ids:
        return totals_map

    roll_up = {pid: pid for pid in product_ids}
    for child_id, parent_id in Product.objects.filter(
        parent_id__in=product_ids
    ).values_list("id", "parent_id"):
        roll_up[child_id] = parent_id

    sale_rows = (
        Sale.objects.filter(client_id__in=client_ids, product_ref_id__in=roll_up)
        .values("client_id", "product_ref_id")
        .annotate(total=Sum("amount"))
    )
    renewal_rows = (
        Renewal.objects.filter(client_id__in=client_ids, product_ref_id__in=roll_up)
        .values("client_id", "product_ref_id")
        .annotate(total=Sum("premium_amount"))
    )

    for row in sale_rows:
        key = (row["client_id"], roll_up[row["product_ref_id"]])
        totals_map[key] = totals_map.get(key, Decimal("0")) + (row["total"] or Decimal("0"))
    for row in renewal_rows:
        key = (row["client_id"], roll_up[row["product_ref_id"]])
        totals_map[key] = totals_map.get(key, Decimal("0")) + (row["total"] or Decimal("0"))

    return totals_map


def _apply_client_product_filters(clients_qs, product_filters):
    if not product_filters:
        return clients_qs

    scoped_client_ids = list(clients_qs.values_list("id", flat=True))
    product_ids = [meta["product"].id for meta in product_filters]
    totals_map = _client_product_totals_map(scoped_client_ids, product_ids)

    candidate_ids = set(scoped_client_ids)

    for meta in product_filters:
        status_value = meta["status_value"]
        min_value = meta["min_value"]
        max_value = meta["max_value"]
        product_id = meta["product"].id

        if status_value in ["yes", "no"]:
            if status_value == "yes":
                matched_ids = {
                    cid for cid in candidate_ids
                    if totals_map.get((cid, product_id), Decimal("0")) > 0
                }
            else:
                matched_ids = {
                    cid for cid in candidate_ids
                    if totals_map.get((cid, product_id), Decimal("0")) <= 0
                }
            candidate_ids &= matched_ids

        if min_value:
            try:
                min_amount = Decimal(str(min_value))
                matched_ids = {
                    cid for cid in candidate_ids
                    if totals_map.get((cid, product_id), Decimal("0")) >= min_amount
                }
                candidate_ids &= matched_ids
            except Exception:
                pass

        if max_value:
            try:
                max_amount = Decimal(str(max_value))
                matched_ids = {
                    cid for cid in candidate_ids
                    if totals_map.get((cid, product_id), Decimal("0")) <= max_amount
                }
                candidate_ids &= matched_ids
            except Exception:
                pass

    if not candidate_ids:
        return clients_qs.none()
    return clients_qs.filter(id__in=candidate_ids)


def _attach_client_product_badges(clients, product_filters):
    client_ids = [client.id for client in clients]
    product_ids = [meta["product"].id for meta in product_filters]
    totals_map = _client_product_totals_map(client_ids, product_ids)

    for client in clients:
        badges = []
        status_map = {}
        for meta in product_filters:
            total_amount = totals_map.get((client.id, meta["product"].id), Decimal("0"))
            is_active = bool(total_amount > 0)
            status_map[meta["status_field"]] = is_active
            if is_active:
                badges.append({
                    "name": meta["product"].name,
                    "badge_class": meta["badge_class"],
                })
        client.dynamic_product_badges = badges
        client.dynamic_product_status_map = status_map


def _client_kpis(request):
    """Headline counts for the clients KPI strip — one aggregate query.

    Each tile links back into the list with the matching filter applied, so
    the strip doubles as the coarse filter row.
    """
    base = reverse("clients:all_clients")
    agg = Client.objects.aggregate(
        total=Count("id"),
        mapped=Count("id", filter=Q(mapped_to__isnull=False)),
        unmapped=Count("id", filter=Q(mapped_to__isnull=True)),
        sip=Count("id", filter=Q(sip_status=True)),
        aum=Sum("lumsum_investment"),
    )
    current = request.GET.get("mapped_to", "")
    return [
        {"label": "All Clients", "value": agg["total"], "color": "#4338CA",
         "url": base, "active": not current},
        {"label": "Mapped", "value": agg["mapped"], "color": "#15803D"},
        {"label": "Unmapped", "value": agg["unmapped"], "color": "#BE123C",
         "url": f"{base}?mapped_to=unmapped", "active": current == "unmapped"},
        {"label": "Active SIPs", "value": agg["sip"], "color": "#0369A1"},
        {"label": "Lumpsum AUM", "value": f"₹{inr(agg['aum'] or 0)}", "color": "#B45309"},
    ]


@login_required
def all_clients(request):
    # ── Sorting ──
    ALLOWED_SORT = {
        "name": "name",
        "-name": "-name",
        "id": "id",
        "-id": "-id",
        "sip_amount": "sip_amount",
        "-sip_amount": "-sip_amount",
        "pms_amount": "pms_amount",
        "-pms_amount": "-pms_amount",
        "lumsum_investment": "lumsum_investment",
        "-lumsum_investment": "-lumsum_investment",
        "mapped_to": "mapped_to__user__first_name",
        "-mapped_to": "-mapped_to__user__first_name",
        "created_at": "created_at",
        "-created_at": "-created_at",
    }
    sort_param = request.GET.get("sort", "name")
    order_by = ALLOWED_SORT.get(sort_param, "name")

    clients_qs = Client.objects.select_related("mapped_to", "mapped_to__user").order_by(order_by)

    q = (request.GET.get("q") or "").strip()
    if q:
        clients_qs = clients_qs.filter(
            name_words_q("name", q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(pan__icontains=q)
        )

    product_filters = _build_client_product_filters(request)
    clients_qs = _apply_client_product_filters(clients_qs, product_filters)

    # ── Mapped-to filter ──
    mapped_to_id = request.GET.get("mapped_to")
    if mapped_to_id == "unmapped":
        clients_qs = clients_qs.filter(mapped_to__isnull=True)
    elif mapped_to_id:
        try:
            clients_qs = clients_qs.filter(mapped_to_id=int(mapped_to_id))
        except (ValueError, TypeError):
            pass

    # ── Onboarded-date range (drives the dashboard's "New Clients" drill-down) ──
    created_start = parse_date_param(request.GET.get("created_start"))
    created_end = parse_date_param(request.GET.get("created_end"))
    if created_start:
        clients_qs = clients_qs.filter(created_at__date__gte=created_start)
    if created_end:
        clients_qs = clients_qs.filter(created_at__date__lte=created_end)

    total_count = clients_qs.count()

    paginator = Paginator(clients_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

    _attach_client_product_badges(page_obj.object_list, product_filters)

    current = page_obj.number
    total_pages = paginator.num_pages
    start = max(current - 3, 1)
    end = min(current + 3, total_pages)
    page_range = range(start, end + 1)

    get_params = request.GET.copy()
    if "page" in get_params:
        del get_params["page"]
    base_qs = get_params.urlencode()

    employees = Employee.objects.filter(active=True).select_related("user").order_by("user__first_name")

    context = {
        "crumbs": [{"label": "Clients"}],
        "kpis": _client_kpis(request),
        "clients_page": page_obj,
        "page_range": page_range,
        "total_pages": total_pages,
        "total_count": total_count,
        "q": q,
        "base_qs": base_qs,
        "sort": sort_param,
        "mapped_to_id": mapped_to_id or "",
        "employees": employees,
        "product_filters": product_filters,
        "created_start": created_start,
        "created_end": created_end,
    }
    return render(request, "clients/all_clients.html", context)


@login_required
def my_clients(request):
    if not hasattr(request.user, "employee"):
        # No "home" route exists — send them somewhere real instead of 500ing.
        messages.error(request, "You are not assigned as an employee.")
        return redirect("clients:all_clients")

    employee = request.user.employee
    clients_qs = Client.objects.filter(mapped_to=employee).order_by("id")

    q = (request.GET.get("q") or "").strip()
    if q:
        clients_qs = clients_qs.filter(
            name_words_q("name", q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(pan__icontains=q)
        )

    product_filters = _build_client_product_filters(request)
    clients_qs = _apply_client_product_filters(clients_qs, product_filters)

    edited_filter_active = request.GET.get("edited") == "1"
    if edited_filter_active:
        clients_qs = clients_qs.filter(edited_at__isnull=False)

    paginator = Paginator(clients_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

    _attach_client_product_badges(page_obj.object_list, product_filters)

    current = page_obj.number
    total_pages = paginator.num_pages
    start = max(current - 3, 1)
    end = min(current + 3, total_pages)
    page_range = range(start, end + 1)

    get_params = request.GET.copy()
    if "page" in get_params:
        del get_params["page"]
    base_qs = get_params.urlencode()
    qs_without_edited = get_params.copy()
    if "edited" in qs_without_edited:
        del qs_without_edited["edited"]
    edited_toggle_qs = qs_without_edited.urlencode()

    templates = MessageTemplate.objects.all()
    mine = Client.objects.filter(mapped_to=employee)
    agg = mine.aggregate(
        total=Count("id"),
        sip=Count("id", filter=Q(sip_status=True)),
        pms=Count("id", filter=Q(pms_status=True)),
        aum=Sum("lumsum_investment"),
    )
    context = {
        "kpis": [
            {"label": "My Clients", "value": agg["total"], "color": "#4338CA"},
            {"label": "Active SIPs", "value": agg["sip"], "color": "#0369A1"},
            {"label": "PMS Clients", "value": agg["pms"], "color": "#7E22CE"},
            {"label": "Lumpsum AUM", "value": f"\u20b9{inr(agg['aum'] or 0)}", "color": "#B45309"},
        ],
        "clients_page": page_obj,
        "page_range": page_range,
        "total_pages": total_pages,
        "base_qs": base_qs,
        "q": q,
        "edited_filter_active": edited_filter_active,
        "edited_toggle_qs": edited_toggle_qs,
        "templates": templates,
        "product_filters": product_filters,
    }
    return render(request, "clients/my_clients.html", context)


@login_required
def add_client(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save()
            messages.success(request, "Client added successfully!")
            if client.pan:
                from ..services import rta_feed
                linked = rta_feed.relink_folios()
                if linked:
                    messages.info(request, f"{linked} RTA folio/SIP record(s) matched this PAN and were linked.")
            drive_created = False
            if request.POST.get("create_drive_folder"):
                try:
                    folder_id, folder_url = get_or_create_client_folder(client.name, client.id)
                    client.drive_folder_id = folder_id
                    client.drive_folder_url = folder_url
                    client.save(update_fields=["drive_folder_id", "drive_folder_url"])
                    drive_created = True
                    messages.success(request, "Google Drive folder created — opening it in a new tab.")
                except DriveNotConfigured as e:
                    messages.warning(request, f"Client saved, but Drive folder skipped: {e}")
                except Exception as e:
                    messages.warning(request, f"Client saved, but Drive folder failed: {e}")
            # Land on the new client's profile (so the team can see/edit immediately).
            # If the Drive folder was just created, the profile auto-opens it in a new tab.
            target = reverse("clients:client_profile", args=[client.id])
            if drive_created:
                target += "?open_drive=1"
            return redirect(target)
    else:
        form = ClientForm()
    return render(request, "clients/add_client.html", {"form": form})


@login_required
def edit_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    user_emp = getattr(request.user, "employee", None)
    role = getattr(user_emp, "role", None)
    is_admin = permissions.is_admin(request.user)
    is_employee = bool(user_emp and role == "employee")
    if is_employee and client.mapped_to != user_emp:
        messages.error(request, "You can edit only your assigned clients.")
        return redirect("clients:my_clients")

    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if not is_admin and "mapped_to" in form.fields:
            form.fields.pop("mapped_to")
        if form.is_valid():
            updated = form.save(commit=False)
            if not is_admin:
                updated.mapped_to = client.mapped_to
            updated.lumsum_investment = form.cleaned_data.get("lumsum_investment")
            updated.edited_at = timezone.now()
            if user_emp:
                updated.edited_by = user_emp
            updated.save()
            messages.success(request, "Client updated successfully!")
            if "pan" in form.changed_data and updated.pan:
                from ..services import rta_feed
                linked = rta_feed.relink_folios()
                if linked:
                    messages.info(request, f"{linked} RTA folio/SIP record(s) matched this PAN and were linked.")
            if not is_employee:
                return redirect("clients:all_clients")
            return redirect("clients:my_clients")
    else:
        form = ClientForm(instance=client)
        if not is_admin and "mapped_to" in form.fields:
            form.fields.pop("mapped_to")

    return render(request, "clients/edit_client.html", {"form": form, "client": client})


@login_required
def client_profile(request, client_id):
    client = get_object_or_404(
        Client.objects.select_related("mapped_to__user"), id=client_id
    )

    sales = (
        Sale.objects.filter(client=client)
        .select_related("employee__user")
        .order_by("-date", "-id")
    )
    renewals = (
        Renewal.objects.filter(client=client)
        .select_related("employee__user", "product_ref")
        .order_by("-renewal_date")
    )
    sales_summary = sales.aggregate(total_amount=Sum("amount"), total_points=Sum("points"))

    # Work about this client: open tasks first, recent history below.
    from ..models import Task
    open_tasks = (
        Task.objects.filter(client=client, is_deleted=False)
        .exclude(status__in=[Task.STATUS_COMPLETED, Task.STATUS_CANCELLED])
        .select_related("assigned_to__user").order_by("due_date", "due_time")
    )

    from django.db.models import Count
    from ..models import SipRegistration

    # What this client actually holds with us. The Portfolio cards show the
    # derived health/life *summary* recomputed from approved sales; they never
    # named a policy, so the profile could not answer "which policy, and when
    # does it renew" — the question anyone opening a client asks.
    # The Insurance tab reads the tracker, which is a **persisted** record:
    # InsurancePolicy rows are created once, when a sale is approved or a
    # renewal is logged, and updated in place. Nothing here rescans the sales
    # or renewals tables — two queries serve the whole tab however long the
    # client's history is.
    from django.db.models import Prefetch
    from ..models import Renewal as _Renewal
    policies = (
        client.policies
        .select_related("relationship_manager__user", "source_sale")
        .annotate(renewal_count=Count("renewals"))
        .prefetch_related(Prefetch(
            "renewals",
            queryset=_Renewal.objects.select_related("employee__user")
                                     .order_by("-premium_collected_on"),
        ))
        .order_by("insurance_type", "end_date")
    )
    insurance_policies = [p for p in policies
                          if p.insurance_type in ("health", "life")]
    from ..services import rta_feed
    mf_folios = (
        client.mf_folios.select_related("arn")
        .annotate(txn_count=Count("transactions"))
        .order_by("amc_name", "folio_number")
    )
    # Match via the reg's own client OR its folio's client — a folio linked
    # after the registration was imported leaves reg.client null.
    mf_sips = (
        SipRegistration.objects
        .filter(Q(client=client) | Q(folio__client=client))
        .order_by("status", "-registered_on", "-first_seen_at")
    )
    mf_summary = rta_feed.mf_summary_for_client(client)

    # What the client holds, grouped by product line, straight off the sales
    # book and the renewals against it — one row per policy, with its number.
    # The RTA feed is not consulted here (owner's call 2026-09-02); its own
    # sections lower down still show the feed's view, labelled as such.
    from ..services import holdings as holdings_service
    portfolio = holdings_service.portfolio(client)
    by_code = {line["code"]: line for line in portfolio}

    def _line_total(code):
        line = by_code.get(code)
        if not line:
            return 0
        return line["cover"] if line["is_cover"] else line["amount"]

    kpis = [
        {"label": "SIP", "color": "#0369A1", "value": f"₹{inr(_line_total('SIP'))}",
         "sub": f"{by_code['SIP']['count']} sale(s)" if "SIP" in by_code else None},
        {"label": "Lumpsum", "color": "#B45309", "value": f"₹{inr(_line_total('LUMSUM'))}",
         "sub": f"{by_code['LUMSUM']['count']} sale(s)" if "LUMSUM" in by_code else None},
        {"label": "Health Cover", "color": "#15803D",
         "value": f"₹{inr(_line_total('HEALTH_INS'))}",
         "sub": f"{by_code['HEALTH_INS']['count']} record(s)" if "HEALTH_INS" in by_code else None},
        {"label": "Life Cover", "color": "#7E22CE",
         "value": f"₹{inr(_line_total('LIFE_INS'))}",
         "sub": f"{by_code['LIFE_INS']['count']} record(s)" if "LIFE_INS" in by_code else None},
        {"label": "Open Tasks", "color": "#BE123C", "value": open_tasks.count()},
    ]


    return render(request, "clients/client_profile.html", {
        "crumbs": [
            {"label": "Clients", "url": reverse("clients:all_clients")},
            {"label": client.name},
        ],
        "kpis": kpis,
        "client": client,
        "sales": sales,
        "renewals": renewals,
        "open_tasks": open_tasks,
        "mf_folios": mf_folios,
        "mf_sips": mf_sips,
        "mf_summary": mf_summary,
        "policies": policies,
        "insurance_policies": insurance_policies,
        "policies_cover": sum((p.sum_insured or 0) for p in policies),
        "policies_premium": sum((p.premium_amount or 0) for p in policies),
        "policies_renewals": sum(p.renewal_count for p in policies),
        "portfolio": portfolio,
        "sales_total_amount": sales_summary.get("total_amount") or 0,
        "sales_total_points": sales_summary.get("total_points") or 0,
    })


@login_required
def client_drive_folder(request, client_id):
    """Ensure a Drive folder exists for this client; redirect to it.

    Stores the folder id/url on the Client so subsequent clicks are instant.
    """
    client = get_object_or_404(Client, id=client_id)

    if client.drive_folder_url:
        return redirect(client.drive_folder_url)

    try:
        folder_id, folder_url = get_or_create_client_folder(client.name, client.id)
    except DriveNotConfigured as e:
        messages.error(request, str(e))
        return redirect("clients:client_profile", client_id=client.id)
    except Exception as e:
        messages.error(request, f"Could not create Drive folder: {e}")
        return redirect("clients:client_profile", client_id=client.id)

    client.drive_folder_id = folder_id
    client.drive_folder_url = folder_url
    client.save(update_fields=["drive_folder_id", "drive_folder_url"])
    return redirect(folder_url)


@login_required
def search_clients(request):
    query = request.GET.get("q", "")
    clients = Client.objects.filter(
        name_words_q("name", query) | Q(email__icontains=query) | Q(phone__icontains=query)
    )[:10]
    results = [
        {"id": c.id, "text": f"{c.name} ({c.email or ''} {c.phone or ''})"}
        for c in clients
    ]
    return JsonResponse({"results": results})


def _is_admin(user):
    return permissions.is_admin(user)


@user_passes_test(_is_admin)
def map_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)
    employees = Employee.objects.all()

    if request.method == "POST":
        emp_id = request.POST.get("employee")
        if emp_id:
            emp = get_object_or_404(Employee, id=emp_id)
            client.mapped_to = emp
            client.status = "Mapped"
            client.save()
            messages.success(request, f"Client {client.name} mapped to {emp.user.username}")
        else:
            client.mapped_to = None
            client.status = "Unmapped"
            client.save()
            messages.success(request, f"Client {client.name} unmapped")
        return redirect("clients:all_clients")

    return render(request, "clients/map_client.html", {"client": client, "employees": employees})


@login_required
def client_analysis(request):
    user_emp = getattr(request.user, "employee", None)
    if permissions.is_admin(request.user):
        clients = Client.objects.all()
    elif user_emp:
        clients = Client.objects.filter(mapped_to=user_emp)
    else:
        return HttpResponseForbidden("Need an employee account.")

    product_filters = _build_client_product_filters(request)
    clients = _apply_client_product_filters(clients, product_filters)

    start_date = parse_date_param(request.GET.get("start_date"))
    end_date = parse_date_param(request.GET.get("end_date"))
    if start_date and end_date:
        clients = clients.filter(created_at__range=[start_date, end_date])

    if "export" in request.GET:
        import csv

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="clients_analysis.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Name", "Email", "Phone", *[meta["product"].name for meta in product_filters], "Created At"])

        analysis_clients = list(clients)
        _attach_client_product_badges(analysis_clients, product_filters)
        for c in analysis_clients:
            writer.writerow([
                c.id,
                c.name,
                c.email,
                c.phone,
                *[("Yes" if c.dynamic_product_status_map.get(meta["status_field"]) else "No") for meta in product_filters],
                c.created_at.strftime("%Y-%m-%d"),
            ])
        return response

    _attach_client_product_badges(clients, product_filters)
    analysis_colspan = 5 + len(product_filters)
    return render(
        request,
        "clients/client_analysis.html",
        {
            "clients": clients,
            "product_filters": product_filters,
            "analysis_colspan": analysis_colspan,
        },
    )


@login_required
@permission_required("clients.change_client", raise_exception=True)
def client_reassign_view(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    if request.method == "POST":
        form = ClientReassignForm(request.POST)
        if form.is_valid():
            new_employee = form.cleaned_data["new_employee"]
            note = form.cleaned_data.get("note", "")
            changed, previous, new = client.reassign_to(
                new_employee, changed_by=request.user, note=note
            )
            if changed:
                messages.success(
                    request,
                    f"Reassigned client to {new_employee.user.username if new_employee else 'Unassigned'}.",
                )
            else:
                messages.info(request, "No change — client already assigned to that employee.")
            return redirect(reverse("clients:detail", args=[client.id]))
    else:
        form = ClientReassignForm(initial={"new_employee": client.mapped_to})

    return render(request, "clients/reassign_modal.html", {"client": client, "form": form})


@login_required
def bulk_reassign_view(request):
    emp = getattr(request.user, "employee", None)
    if not permissions.is_admin_or_manager(request.user):
        return redirect("clients:employee_dashboard")

    employees = Employee.objects.filter(active=True).select_related("user")
    context = {
        "employees": employees,
        "clients_preview": None,
        "source_emp": None,
        "target_emp": None,
        "mode": None,
        "q": "",
    }

    if request.method == "POST":
        action = request.POST.get("action")
        q = (request.POST.get("q") or "").strip()
        context["q"] = q

        target_emp = None
        target_id_val = request.POST.get("target_employee")
        if target_id_val:
            try:
                target_emp = Employee.objects.select_related("user").filter(pk=int(target_id_val)).first()
            except (TypeError, ValueError):
                target_emp = None
        context["target_emp"] = target_emp

        if action == "load":
            try:
                source_id = int(request.POST.get("source_employee") or 0)
            except (TypeError, ValueError):
                messages.error(request, "Please choose a source employee.")
                return render(request, "clients/bulk_reassign.html", context)

            source_emp = Employee.objects.select_related("user").filter(pk=source_id).first()
            if not source_emp:
                messages.error(request, "Source employee not found.")
                return render(request, "clients/bulk_reassign.html", context)
            clients_qs = Client.objects.filter(mapped_to=source_emp).order_by("id")
            if q:
                clients_qs = clients_qs.filter(
                    name_words_q("name", q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(pan__icontains=q)
                )
            context.update({"clients_preview": clients_qs, "source_emp": source_emp, "mode": "mapped"})
            return render(request, "clients/bulk_reassign.html", context)

        if action == "load_unmapped":
            clients_qs = Client.objects.filter(mapped_to__isnull=True).order_by("id")
            if q:
                clients_qs = clients_qs.filter(
                    name_words_q("name", q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(pan__icontains=q)
                )
            context.update({"clients_preview": clients_qs, "source_emp": None, "mode": "unmapped"})
            return render(request, "clients/bulk_reassign.html", context)

        if action == "apply":
            mode = request.POST.get("mode") or "mapped"
            try:
                target_id = int(request.POST.get("target_employee") or 0)
            except (TypeError, ValueError):
                messages.error(request, "Target employee missing.")
                return redirect("clients:bulk_reassign")

            target_emp = Employee.objects.select_related("user").filter(pk=target_id).first()
            if not target_emp:
                messages.error(request, "Target employee not found.")
                return redirect("clients:bulk_reassign")

            selected = request.POST.getlist("selected_client")
            if not selected:
                messages.error(request, "No clients selected for reassignment.")
                return redirect("clients:bulk_reassign")

            if mode == "unmapped":
                clients_to_move = Client.objects.filter(id__in=selected, mapped_to__isnull=True)
                source_emp = None
            else:
                try:
                    source_id = int(request.POST.get("source_employee") or 0)
                except (TypeError, ValueError):
                    messages.error(request, "Source employee missing.")
                    return redirect("clients:bulk_reassign")
                source_emp = Employee.objects.select_related("user").filter(pk=source_id).first()
                if not source_emp:
                    messages.error(request, "Source employee not found.")
                    return redirect("clients:bulk_reassign")
                clients_to_move = Client.objects.filter(id__in=selected, mapped_to=source_emp)

            if not clients_to_move.exists():
                messages.error(request, "No valid clients found to reassign.")
                return redirect("clients:bulk_reassign")

            moved_count = 0
            with transaction.atomic():
                for c in clients_to_move:
                    changed, prev, new = c.reassign_to(
                        target_emp, changed_by=request.user, note="Bulk reassign via admin page"
                    )
                    if changed:
                        moved_count += 1

            if mode == "unmapped":
                messages.success(
                    request,
                    f"Mapped {moved_count} unmapped client(s) to {target_emp.user.username}.",
                )
            else:
                messages.success(
                    request,
                    f"Reassigned {moved_count} client(s) from {source_emp.user.username} to {target_emp.user.username}.",
                )
            return redirect("clients:bulk_reassign")

    return render(request, "clients/bulk_reassign.html", context)


# ─────────────────────────── families / households ───────────────────────────

@login_required
def family_list(request):
    """Households with combined AUM and wealth band."""
    families = (
        Family.objects.select_related("head", "relationship_manager__user")
        .prefetch_related("members").order_by("name")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        families = families.filter(Q(name__icontains=q) | Q(code__icontains=q))

    rows = list(families)
    total_households = len(rows)
    # AUM and band are derived from members — compute once, here, so the
    # template and the KPI strip agree.
    band_counts = {label: 0 for label, _ in Family.CATEGORY_BANDS}
    for f in rows:
        f.total_aum = f.aum
        f.band = f.category
        band_counts[f.band] += 1

    wanted = request.GET.get("band", "")
    if wanted:
        rows = [f for f in rows if f.band == wanted]

    base = reverse("clients:family_list")
    colors = ["#15803D", "#4338CA", "#B45309", "#57534E"]
    kpis = [{"label": "All Households", "value": total_households, "color": "#0F766E",
             "url": base, "active": not wanted}]
    for i, (label, _floor) in enumerate(Family.CATEGORY_BANDS):
        kpis.append({
            "label": label.split(" (")[0], "value": band_counts[label],
            "color": colors[i % len(colors)],
            "url": f"{base}?band={quote(label)}", "active": wanted == label,
        })

    return render(request, "clients/families.html", {
        "crumbs": [{"label": "Households"}],
        "kpis": kpis, "families": rows, "q": q, "band": wanted,
    })


@login_required
def family_detail(request, family_id):
    family = get_object_or_404(
        Family.objects.select_related("head", "relationship_manager__user"), pk=family_id)
    members = family.members.select_related("mapped_to__user").order_by("name")
    return render(request, "clients/family_detail.html", {
        "crumbs": [
            {"label": "Households", "url": reverse("clients:family_list")},
            {"label": family.name},
        ],
        "kpis": [
            {"label": "Combined AUM", "value": f"\u20b9{inr(family.aum)}", "color": "#15803D"},
            {"label": "Category", "value": family.category.split(" (")[0], "color": "#4338CA"},
            {"label": "Members", "value": members.count(), "color": "#0369A1"},
        ],
        "family": family, "members": members,
    })

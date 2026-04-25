"""Client management views: list, add, edit, search, map, reassign, analysis."""
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test, permission_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.db.models import Q, Sum
from django.db import transaction
from django.core.paginator import Paginator
from django.urls import reverse
from django.conf import settings

from ..models import Client, Employee, MessageTemplate, Product, Renewal, Sale
from ..forms import ClientForm, ClientReassignForm
from ..services.google_drive import DriveNotConfigured, get_or_create_client_folder


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
    active_products = Product.objects.filter(is_active=True).order_by("display_order", "name")
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
    totals_map = {}
    if not client_ids or not product_ids:
        return totals_map

    sale_rows = (
        Sale.objects.filter(client_id__in=client_ids, product_ref_id__in=product_ids)
        .values("client_id", "product_ref_id")
        .annotate(total=Sum("amount"))
    )
    renewal_rows = (
        Renewal.objects.filter(client_id__in=client_ids, product_ref_id__in=product_ids)
        .values("client_id", "product_ref_id")
        .annotate(total=Sum("premium_amount"))
    )

    for row in sale_rows:
        key = (row["client_id"], row["product_ref_id"])
        totals_map[key] = totals_map.get(key, Decimal("0")) + (row["total"] or Decimal("0"))
    for row in renewal_rows:
        key = (row["client_id"], row["product_ref_id"])
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
            Q(name__icontains=q)
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
    }
    return render(request, "clients/all_clients.html", context)


@login_required
def my_clients(request):
    if not hasattr(request.user, "employee"):
        messages.error(request, "You are not assigned as an employee.")
        return redirect("home")

    employee = request.user.employee
    clients_qs = Client.objects.filter(mapped_to=employee).order_by("id")

    q = (request.GET.get("q") or "").strip()
    if q:
        clients_qs = clients_qs.filter(
            Q(name__icontains=q)
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
    context = {
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
            form.save()
            messages.success(request, "Client added successfully!")
            return redirect("clients:all_clients")
    else:
        form = ClientForm()
    return render(request, "clients/add_client.html", {"form": form})


@login_required
def edit_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    user_emp = getattr(request.user, "employee", None)
    role = getattr(user_emp, "role", None)
    is_admin = request.user.is_superuser or role == "admin"
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

    return render(request, "clients/client_profile.html", {
        "client": client,
        "sales": sales,
        "renewals": renewals,
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
        Q(name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query)
    )[:10]
    results = [
        {"id": c.id, "text": f"{c.name} ({c.email or ''} {c.phone or ''})"}
        for c in clients
    ]
    return JsonResponse({"results": results})


def _is_admin(user):
    return hasattr(user, "employee") and user.employee.role == "admin"


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
    if request.user.employee.role == "admin":
        clients = Client.objects.all()
    else:
        clients = Client.objects.filter(mapped_to=request.user.employee)

    product_filters = _build_client_product_filters(request)
    clients = _apply_client_product_filters(clients, product_filters)

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
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
    if not (request.user.is_superuser or (emp and emp.role in ("admin", "manager"))):
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
                    Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(pan__icontains=q)
                )
            context.update({"clients_preview": clients_qs, "source_emp": source_emp, "mode": "mapped"})
            return render(request, "clients/bulk_reassign.html", context)

        if action == "load_unmapped":
            clients_qs = Client.objects.filter(mapped_to__isnull=True).order_by("id")
            if q:
                clients_qs = clients_qs.filter(
                    Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(pan__icontains=q)
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

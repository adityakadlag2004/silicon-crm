"""Client management views: list, add, edit, search, map, reassign, analysis."""
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test, permission_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.db import transaction
from django.core.paginator import Paginator
from django.urls import reverse
from django.conf import settings

from ..models import Client, Employee, MessageTemplate
from ..forms import ClientForm, ClientReassignForm


PER_PAGE = getattr(settings, "PER_PAGE", 50)


@login_required
def all_clients(request):
    clients_qs = Client.objects.select_related("mapped_to").order_by("id")

    q = (request.GET.get("q") or "").strip()
    if q:
        clients_qs = clients_qs.filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(pan__icontains=q)
        )

    sip_status = request.GET.get("sip_status")
    pms_status = request.GET.get("pms_status")
    life_status = request.GET.get("life_status")
    health_status = request.GET.get("health_status")
    sip_min = request.GET.get("sip_min")
    sip_max = request.GET.get("sip_max")
    pms_min = request.GET.get("pms_min")
    pms_max = request.GET.get("pms_max")
    life_min = request.GET.get("life_min")
    life_max = request.GET.get("life_max")
    health_min = request.GET.get("health_min")
    health_max = request.GET.get("health_max")

    if sip_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(sip_status=(sip_status == "yes"))
    if pms_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(pms_status=(pms_status == "yes"))
    if life_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(life_status=(life_status == "yes"))
    if health_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(health_status=(health_status == "yes"))

    for field, fmin, fmax in [
        ("sip_amount", sip_min, sip_max),
        ("pms_amount", pms_min, pms_max),
        ("life_cover", life_min, life_max),
        ("health_cover", health_min, health_max),
    ]:
        if fmin:
            try:
                clients_qs = clients_qs.filter(**{f"{field}__gte": float(fmin)})
            except ValueError:
                pass
        if fmax:
            try:
                clients_qs = clients_qs.filter(**{f"{field}__lte": float(fmax)})
            except ValueError:
                pass

    paginator = Paginator(clients_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

    current = page_obj.number
    total_pages = paginator.num_pages
    start = max(current - 3, 1)
    end = min(current + 3, total_pages)
    page_range = range(start, end + 1)

    get_params = request.GET.copy()
    if "page" in get_params:
        del get_params["page"]
    base_qs = get_params.urlencode()

    templates = MessageTemplate.objects.all()
    context = {
        "clients_page": page_obj,
        "page_range": page_range,
        "total_pages": total_pages,
        "sip_status": sip_status,
        "pms_status": pms_status,
        "life_status": life_status,
        "health_status": health_status,
        "sip_min": sip_min,
        "sip_max": sip_max,
        "pms_min": pms_min,
        "pms_max": pms_max,
        "life_min": life_min,
        "life_max": life_max,
        "health_min": health_min,
        "health_max": health_max,
        "q": q,
        "base_qs": base_qs,
        "templates": templates,
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

    sip_status = request.GET.get("sip_status")
    pms_status = request.GET.get("pms_status")
    life_status = request.GET.get("life_status")
    health_status = request.GET.get("health_status")
    sip_min = request.GET.get("sip_min")
    sip_max = request.GET.get("sip_max")
    pms_min = request.GET.get("pms_min")
    pms_max = request.GET.get("pms_max")
    life_min = request.GET.get("life_min")
    life_max = request.GET.get("life_max")
    health_min = request.GET.get("health_min")
    health_max = request.GET.get("health_max")

    if sip_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(sip_status=(sip_status == "yes"))
    if pms_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(pms_status=(pms_status == "yes"))
    if life_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(life_status=(life_status == "yes"))
    if health_status in ["yes", "no"]:
        clients_qs = clients_qs.filter(health_status=(health_status == "yes"))

    for field, fmin, fmax in [
        ("sip_amount", sip_min, sip_max),
        ("pms_amount", pms_min, pms_max),
        ("life_cover", life_min, life_max),
        ("health_cover", health_min, health_max),
    ]:
        if fmin:
            try:
                clients_qs = clients_qs.filter(**{f"{field}__gte": float(fmin)})
            except ValueError:
                pass
        if fmax:
            try:
                clients_qs = clients_qs.filter(**{f"{field}__lte": float(fmax)})
            except ValueError:
                pass

    edited_filter_active = request.GET.get("edited") == "1"
    if edited_filter_active:
        clients_qs = clients_qs.filter(edited_at__isnull=False)

    paginator = Paginator(clients_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

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
        "sip_status": sip_status,
        "pms_status": pms_status,
        "life_status": life_status,
        "health_status": health_status,
        "sip_min": sip_min,
        "sip_max": sip_max,
        "pms_min": pms_min,
        "pms_max": pms_max,
        "life_min": life_min,
        "life_max": life_max,
        "health_min": health_min,
        "health_max": health_max,
        "edited_filter_active": edited_filter_active,
        "edited_toggle_qs": edited_toggle_qs,
        "templates": templates,
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

    filters = {}
    for field in ("sip_status", "life_status", "health_status", "motor_status", "pms_status"):
        val = request.GET.get(field)
        if val in ["yes", "no"]:
            filters[field] = val == "yes"
    clients = clients.filter(**filters)

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    if start_date and end_date:
        clients = clients.filter(created_at__range=[start_date, end_date])

    if "export" in request.GET:
        import csv

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="clients_analysis.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["ID", "Name", "Email", "Phone", "SIP", "Life", "Health", "Motor", "PMS", "Created At"]
        )
        for c in clients:
            writer.writerow([
                c.id, c.name, c.email, c.phone,
                "Yes" if c.sip_status else "No",
                "Yes" if c.life_status else "No",
                "Yes" if c.health_status else "No",
                "Yes" if c.motor_status else "No",
                "Yes" if c.pms_status else "No",
                c.created_at.strftime("%Y-%m-%d"),
            ])
        return response

    return render(request, "clients/client_analysis.html", {"clients": clients})


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
    employees = Employee.objects.all().select_related("user")
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

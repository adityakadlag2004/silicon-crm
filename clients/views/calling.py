"""Calling list views."""
import csv
import io
from datetime import datetime, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.db import transaction
from django.db.models import Sum
from django.core.paginator import Paginator

from ..models import (
    Client,
    Sale,
    Employee,
    CalendarEvent,
    CallRecord,
    CallingList,
    Prospect,
)
from .helpers import get_manager_access


@login_required
def calling_list_generator(request):
    """Generate calling lists by applying filters.

    Filters supported (GET):
    - sip: all | yes | no
    - product: one of Sale.PRODUCT_CHOICES or empty
    - min_amount, max_amount: numeric filters applied to SIP amount (for SIP) or Sale.amount (for Sales)
    - mapped: employee id to filter clients mapped to a specific employee
    - unmapped_only: if present, only clients with mapped_to is null

    Actions (POST):
    - export_csv: export current results
    - create_list: create a CallingList with selected client ids and optional assign_employee
    """
    sip_status = request.GET.get('sip', 'any')
    sip_min = request.GET.get('sip_min')
    sip_max = request.GET.get('sip_max')

    health_status = request.GET.get('health', 'any')
    health_min = request.GET.get('health_min')
    health_max = request.GET.get('health_max')

    life_status = request.GET.get('life', 'any')
    life_min = request.GET.get('life_min')
    life_max = request.GET.get('life_max')

    motor_status = request.GET.get('motor', 'any')
    motor_min = request.GET.get('motor_min')
    motor_max = request.GET.get('motor_max')

    pms_status = request.GET.get('pms', 'any')
    pms_min = request.GET.get('pms_min')
    pms_max = request.GET.get('pms_max')

    mapped_emp = request.GET.get('mapped')
    unmapped_only = request.GET.get('unmapped_only')
    source = request.GET.get('source', 'clients')

    qs = None
    if source == 'prospects':
        qs = Prospect.objects.select_related('assigned_to').all()
    else:
        qs = Client.objects.all()

    if hasattr(request.user, 'employee') and request.user.employee.role == 'employee':
        emp = request.user.employee
        if source == 'prospects':
            qs = qs.filter(assigned_to=emp)
        else:
            qs = qs.filter(mapped_to=emp)

    # SIP filter and amount
    try:
        sip_min_v = float(sip_min) if sip_min not in (None, '') else None
    except Exception:
        sip_min_v = None
    try:
        sip_max_v = float(sip_max) if sip_max not in (None, '') else None
    except Exception:
        sip_max_v = None

    if sip_status == 'yes':
        qs = qs.filter(sip_status=True)
        if sip_min_v is not None:
            qs = qs.filter(sip_amount__gte=sip_min_v)
        if sip_max_v is not None:
            qs = qs.filter(sip_amount__lte=sip_max_v)
    elif sip_status == 'no':
        qs = qs.filter(sip_status=False)

    if mapped_emp:
        try:
            emp = Employee.objects.get(id=int(mapped_emp))
            if source == 'prospects':
                qs = qs.filter(assigned_to=emp)
            else:
                qs = qs.filter(mapped_to=emp)
        except Exception:
            pass

    if unmapped_only:
        if source == 'prospects':
            qs = qs.filter(assigned_to__isnull=True)
        else:
            qs = qs.filter(mapped_to__isnull=True)

    # Health filters
    try:
        health_min_v = float(health_min) if health_min not in (None, '') else None
    except Exception:
        health_min_v = None
    try:
        health_max_v = float(health_max) if health_max not in (None, '') else None
    except Exception:
        health_max_v = None

    if health_status == 'yes':
        qs = qs.filter(health_status=True)
        if health_min_v is not None:
            qs = qs.filter(health_cover__gte=health_min_v)
        if health_max_v is not None:
            qs = qs.filter(health_cover__lte=health_max_v)
    elif health_status == 'no':
        qs = qs.filter(health_status=False)

    # Life filters
    try:
        life_min_v = float(life_min) if life_min not in (None, '') else None
    except Exception:
        life_min_v = None
    try:
        life_max_v = float(life_max) if life_max not in (None, '') else None
    except Exception:
        life_max_v = None

    if life_status == 'yes':
        qs = qs.filter(life_status=True)
        if life_min_v is not None:
            qs = qs.filter(life_cover__gte=life_min_v)
        if life_max_v is not None:
            qs = qs.filter(life_cover__lte=life_max_v)
    elif life_status == 'no':
        qs = qs.filter(life_status=False)

    # Motor filters
    try:
        motor_min_v = float(motor_min) if motor_min not in (None, '') else None
    except Exception:
        motor_min_v = None
    try:
        motor_max_v = float(motor_max) if motor_max not in (None, '') else None
    except Exception:
        motor_max_v = None

    if motor_status == 'yes':
        qs = qs.filter(motor_status=True)
        if motor_min_v is not None:
            qs = qs.filter(motor_insured_value__gte=motor_min_v)
        if motor_max_v is not None:
            qs = qs.filter(motor_insured_value__lte=motor_max_v)
    elif motor_status == 'no':
        qs = qs.filter(motor_status=False)

    # PMS filters
    try:
        pms_min_v = float(pms_min) if pms_min not in (None, '') else None
    except Exception:
        pms_min_v = None
    try:
        pms_max_v = float(pms_max) if pms_max not in (None, '') else None
    except Exception:
        pms_max_v = None

    if pms_status == 'yes':
        qs = qs.filter(pms_status=True)
        if pms_min_v is not None:
            qs = qs.filter(pms_amount__gte=pms_min_v)
        if pms_max_v is not None:
            qs = qs.filter(pms_amount__lte=pms_max_v)
    elif pms_status == 'no':
        qs = qs.filter(pms_status=False)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 25))
    order_expr = '-created_at'
    paginator = Paginator(qs.order_by(order_expr), per_page)
    page_obj = paginator.get_page(page)

    params = request.GET.copy()
    if 'page' in params:
        params.pop('page')
    qs_params = params.urlencode()

    can_create = False
    if request.user.is_superuser:
        can_create = True
    elif hasattr(request.user, 'employee'):
        can_create = True

    if request.method == 'POST':
        action = request.POST.get('action')
        selected_ids = request.POST.getlist('selected_ids')
        if action == 'export_csv':
            if selected_ids:
                export_qs = Client.objects.filter(id__in=selected_ids)
            else:
                export_qs = qs
            resp = HttpResponse(content_type='text/csv')
            resp['Content-Disposition'] = 'attachment; filename="calling_list.csv"'
            writer = csv.writer(resp)
            writer.writerow(['client_id', 'name', 'phone', 'email', 'mapped_to', 'sip_status', 'sip_amount'])
            for c in export_qs.order_by('id'):
                writer.writerow([c.id, c.name, c.phone or '', c.email or '', getattr(c.mapped_to, 'user', '') and getattr(c.mapped_to.user, 'username', '') or '', c.sip_status, c.sip_amount or ''])
            return resp

        elif action == 'create_list':
            if not can_create:
                messages.error(request, 'You do not have permission to create calling lists.')
                return redirect(request.path)

            title = request.POST.get('title') or f"Calling List {timezone.now().date()}"
            assign_emp_single = request.POST.get('assign_employee')
            assign_emp_multiple = request.POST.getlist('assign_employee_multiple')
            csv_file = request.FILES.get('csv_file')

            if source == 'prospects':
                if selected_ids:
                    selected_qs = Prospect.objects.filter(id__in=selected_ids)
                else:
                    selected_qs = qs
            else:
                if selected_ids:
                    selected_qs = Client.objects.filter(id__in=selected_ids)
                else:
                    selected_qs = qs

            if not selected_qs.exists() and not csv_file:
                messages.error(request, 'No rows selected and no CSV uploaded to create a calling list')
                return redirect(request.path)

            with transaction.atomic():
                cl = CallingList.objects.create(title=title, uploaded_by=request.user)
                prospects_to_create = []

                if csv_file:
                    try:
                        data = csv_file.read().decode('utf-8')
                        reader = csv.DictReader(io.StringIO(data))
                        for row in reader:
                            name = row.get('name') or row.get('Name') or ''
                            phone = row.get('phone') or row.get('Phone') or ''
                            email = row.get('email') or row.get('Email') or ''
                            p = Prospect(calling_list=cl, assigned_to=None, name=name, phone=phone, email=email)
                            prospects_to_create.append(p)
                    except Exception:
                        messages.warning(request, 'Uploaded CSV could not be parsed; skipping CSV rows.')

                if source == 'prospects':
                    for sp in selected_qs:
                        p = Prospect(calling_list=cl, assigned_to=sp.assigned_to, name=sp.name, phone=sp.phone or '', email=sp.email or '', notes=sp.notes)
                        prospects_to_create.append(p)
                else:
                    for c in selected_qs:
                        p = Prospect(calling_list=cl, assigned_to=None, name=c.name, phone=c.phone or '', email=c.email or '')
                        prospects_to_create.append(p)

                Prospect.objects.bulk_create(prospects_to_create)

                user_emp = getattr(request.user, 'employee', None)
                if user_emp and getattr(user_emp, 'role', None) == 'employee':
                    Prospect.objects.filter(calling_list=cl).update(assigned_to=user_emp)
                else:
                    if assign_emp_multiple:
                        emp_objs = []
                        for eid in assign_emp_multiple:
                            try:
                                emp_objs.append(Employee.objects.get(id=int(eid)))
                            except Exception:
                                continue
                        if emp_objs:
                            created = list(Prospect.objects.filter(calling_list=cl).order_by('id'))
                            for i, created_p in enumerate(created):
                                assigned_emp = emp_objs[i % len(emp_objs)]
                                created_p.assigned_to = assigned_emp
                                created_p.save(update_fields=['assigned_to'])
                    elif assign_emp_single:
                        try:
                            assigned_emp = Employee.objects.get(id=int(assign_emp_single))
                            Prospect.objects.filter(calling_list=cl).update(assigned_to=assigned_emp)
                        except Exception:
                            pass

            messages.success(request, f'Calling list "{cl.title}" created with {len(prospects_to_create)} prospects')
            return redirect('clients:callingworkspace', list_id=cl.id)

    # compute last sale per client for visible page (clients source)
    last_sales = {}
    if source != 'prospects':
        client_ids = [c.id for c in page_obj.object_list]
        last_qs = Sale.objects.filter(client_id__in=client_ids).order_by('client_id', '-date')
        seen = set()
        for s in last_qs:
            if s.client_id in seen:
                continue
            last_sales[s.client_id] = {'date': s.date, 'product': s.product}
            seen.add(s.client_id)
        for c in page_obj.object_list:
            ls = last_sales.get(c.id)
            if ls:
                setattr(c, 'last_sale_date', ls.get('date'))
                setattr(c, 'last_sale_product', ls.get('product'))
            else:
                setattr(c, 'last_sale_date', None)
                setattr(c, 'last_sale_product', None)

    context = {
        'page_obj': page_obj,
        'paginator': paginator,
        'sip_status': sip_status,
        'sip_min': sip_min or '',
        'sip_max': sip_max or '',
        'health_status': health_status,
        'health_min': health_min or '',
        'health_max': health_max or '',
        'life_status': life_status,
        'life_min': life_min or '',
        'life_max': life_max or '',
        'motor_status': motor_status,
        'motor_min': motor_min or '',
        'motor_max': motor_max or '',
        'pms_status': pms_status,
        'pms_min': pms_min or '',
        'pms_max': pms_max or '',
        'employees': Employee.objects.select_related('user').filter(active=True),
        'products': [c[0] for c in Sale.PRODUCT_CHOICES],
        'mapped_emp': mapped_emp or '',
        'unmapped_only': bool(unmapped_only),
        'source': source,
        'can_create': can_create,
        'last_sales': last_sales,
        'per_page': per_page,
        'qs_params': qs_params,
    }
    is_employee = False
    if hasattr(request.user, 'employee') and getattr(request.user.employee, 'role', None) == 'employee':
        is_employee = True
    context['is_employee'] = is_employee

    return render(request, 'calling/list_generator.html', context)


@login_required
def upload_list(request):
    if request.method == "POST" and request.FILES.get("file"):
        try:
            import pandas as pd
        except Exception:
            pd = None

        file = request.FILES["file"]

        rows = []
        columns = set()
        if pd is not None:
            if file.name.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            rows = df.to_dict(orient='records')
            columns = set(df.columns)
        else:
            if not file.name.endswith('.csv'):
                messages.error(request, 'Excel uploads require pandas. Please upload a CSV or install pandas.')
                return redirect('clients:upload_list')
            decoded = file.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(decoded))
            rows = [r for r in reader]
            columns = set(rows[0].keys()) if rows else set()

        daily_calls = int(request.POST.get("daily_calls", 5))

        selected_emp_ids = request.POST.getlist('employees[]')
        if selected_emp_ids:
            employees = list(Employee.objects.filter(id__in=selected_emp_ids).select_related('user'))
        else:
            employees = list(Employee.objects.filter(role="employee").select_related('user'))

        if not employees:
            messages.error(request, "No employees available to assign calls. Please add employees or select at least one.")
            return redirect('clients:upload_list')

        emp_count = len(employees)
        emp_index = 0

        prospect_objs = []
        for row in rows:
            assigned_to = None

            if 'assigned_to' in columns and row.get('assigned_to'):
                try:
                    assigned_to = Employee.objects.get(user__username=row.get('assigned_to'))
                except Employee.DoesNotExist:
                    assigned_to = None

            if not assigned_to and emp_count > 0:
                assigned_to = employees[emp_index % emp_count]
                emp_index += 1

            prospect_objs.append(Prospect(
                name=(row.get('name') or 'Unknown'),
                phone=(row.get('phone') or ''),
                email=(row.get('email') or ''),
                notes=(row.get('notes') or ''),
                assigned_to=assigned_to,
            ))

        with transaction.atomic():
            calling_list = CallingList.objects.create(
                title=request.POST.get("title", "Untitled List"),
                uploaded_by=request.user,
            )

            for obj in prospect_objs:
                obj.calling_list = calling_list
            Prospect.objects.bulk_create(prospect_objs)

            prospects = list(calling_list.prospects.select_related('assigned_to').all())

            start_date = timezone.now().date()
            if start_date.weekday() in (5, 6):
                start_date += timedelta(days=(7 - start_date.weekday()))

            emp_buckets = {emp.id: [] for emp in employees}
            for p in prospects:
                if p.assigned_to:
                    emp_buckets[p.assigned_to.id].append(p)

            event_objs = []
            for emp_id, plist in emp_buckets.items():
                day_index = 0
                call_index = 0
                current_date = start_date

                for p in plist:
                    if call_index >= daily_calls:
                        call_index = 0
                        day_index += 1
                        current_date = start_date + timedelta(days=day_index)
                        while current_date.weekday() in (5, 6):
                            day_index += 1
                            current_date = start_date + timedelta(days=day_index)

                    scheduled = timezone.make_aware(
                        datetime.combine(current_date, datetime.min.time()) + timedelta(hours=10 + call_index)
                    )

                    event_objs.append(CalendarEvent(
                        employee_id=emp_id,
                        title=f"Call: {p.name}",
                        type="call_followup",
                        related_prospect=p,
                        scheduled_time=scheduled,
                        notes=f"Call {p.name}, Phone: {p.phone}",
                    ))
                    call_index += 1

            if event_objs:
                CalendarEvent.objects.bulk_create(event_objs)

        messages.success(request, f"Calling list '{calling_list.title}' uploaded and tasks assigned!")
        return redirect("clients:admin_lists")

    employees = Employee.objects.filter(role="employee").select_related('user')
    return render(request, "calling/upload_list.html", {"employees": employees})


@login_required
def admin_lists(request):
    calling_lists = CallingList.objects.all().order_by("-created_at").prefetch_related('prospects__assigned_to')

    for cl in calling_lists:
        seen = set()
        names = []
        for p in cl.prospects.all():
            emp = getattr(p, 'assigned_to', None)
            if emp and emp.id not in seen:
                seen.add(emp.id)
                uname = ''
                try:
                    uname = emp.user.get_full_name() or emp.user.username
                except Exception:
                    uname = str(emp)
                names.append(uname)
        cl.assigned_employees_count = len(names)
        cl.assigned_employees = ', '.join(names) if names else ''
        if getattr(cl, 'uploaded_by', None):
            try:
                cl.created_by_display = cl.uploaded_by.get_full_name() or cl.uploaded_by.username
            except Exception:
                cl.created_by_display = str(cl.uploaded_by)
        else:
            cl.created_by_display = ''

    context = {"calling_lists": calling_lists}
    return render(request, "calling/admin_lists.html", context)


@login_required
def admin_list_detail(request, list_id):
    calling_list = get_object_or_404(CallingList, id=list_id)
    prospects = calling_list.prospects.select_related("assigned_to").all()
    employees = Employee.objects.select_related("user").all()

    if request.method == "POST":
        prospect_id = request.POST.get("prospect_id")
        employee_id = request.POST.get("employee_id")

        prospect = get_object_or_404(Prospect, id=prospect_id, calling_list=calling_list)

        if employee_id:
            employee = get_object_or_404(Employee, id=employee_id)
            prospect.assigned_to = employee
        else:
            prospect.assigned_to = None
        prospect.save()

        return HttpResponseRedirect(reverse("clients:admin_list_detail", args=[list_id]))

    context = {
        "calling_list": calling_list,
        "prospects": prospects,
        "employees": employees,
    }
    return render(request, "calling/admin_list_detail.html", context)


@login_required
def employee_lists(request):
    employee = request.user.employee

    my_lists = CallingList.objects.filter(prospects__assigned_to=employee).distinct()
    for clist in my_lists:
        clist.my_prospects_count = clist.prospects.filter(assigned_to=employee).count()

    context = {"my_lists": my_lists}
    return render(request, "calling/employee_lists.html", context)


@login_required
def calling_workspace(request, list_id):
    employee = request.user.employee
    calling_list = get_object_or_404(CallingList, id=list_id)

    prospects_qs = calling_list.prospects.filter(assigned_to=employee).order_by('id')

    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 25))
    paginator = Paginator(prospects_qs, per_page)
    page_obj = paginator.get_page(page)

    if request.method == "POST":
        action = request.POST.get("action")
        prospect_id = request.POST.get("prospect_id")
        prospect = get_object_or_404(Prospect, id=prospect_id, assigned_to=employee)

        if action == "log_call":
            status = request.POST.get("status")
            notes = request.POST.get("notes", "")
            CallRecord.objects.create(
                prospect=prospect,
                employee=employee,
                call_time=timezone.now(),
                status=status,
                notes=notes,
            )
            prospect.status = status
            prospect.save()
            messages.success(request, f"Call logged for {prospect.name}.")

        elif action == "add_followup":
            followup_raw = request.POST.get("followup_date")
            notes = request.POST.get("notes", "")
            if followup_raw:
                followup_dt = parse_datetime(followup_raw)
                if followup_dt and timezone.is_naive(followup_dt):
                    followup_dt = timezone.make_aware(followup_dt)
                if followup_dt:
                    CalendarEvent.objects.create(
                        employee=employee,
                        title=f"Follow-up: {prospect.name}",
                        scheduled_time=followup_dt,
                        type="follow_up",
                        notes=notes,
                        related_prospect=prospect,
                    )
                    messages.success(request, f"Follow-up added for {prospect.name}.")
                else:
                    messages.error(request, "Could not parse follow-up date/time.")
            else:
                messages.error(request, "Follow-up date is required.")

        return redirect("clients:callingworkspace", list_id=list_id)

    context = {
        "calling_list": calling_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "per_page": per_page,
    }
    return render(request, "calling/callingworkspace.html", context)


@login_required
@require_POST
def delete_calling_list(request, list_id):
    calling_list = get_object_or_404(CallingList, id=list_id)

    user_emp = getattr(request.user, "employee", None)
    if not request.user.is_superuser and (not user_emp or user_emp.role != "admin"):
        messages.error(request, "You are not authorized to delete lists.")
        return redirect("clients:admin_lists")

    calling_list.delete()
    messages.success(request, "Calling list deleted successfully!")
    return redirect("clients:admin_lists")


@login_required
def log_result(request, prospect_id):
    prospect = get_object_or_404(Prospect, id=prospect_id)

    if request.method == "POST":
        status = request.POST.get("status")
        notes = request.POST.get("notes")

        prospect.status = status
        prospect.last_contacted = timezone.now()
        if notes:
            prospect.notes = (prospect.notes or "") + f"\n{timezone.now().strftime('%Y-%m-%d %H:%M')}: {notes}"
        prospect.save()

        if status == "follow_up":
            CalendarEvent.objects.create(
                employee=request.user.employee,
                title=f"Follow-up: {prospect.name}",
                scheduled_time=timezone.now() + timedelta(days=1),
                type="follow_up",
                notes=notes,
                related_prospect=prospect,
            )

        messages.success(request, f"Call result logged for {prospect.name}")
        return redirect("clients:callingworkspace", list_id=prospect.calling_list.id)

    return render(request, "calling/log_result.html", {"prospect": prospect})


@login_required
def add_followup(request, prospect_id):
    prospect = get_object_or_404(Prospect, id=prospect_id)

    if request.method == "POST":
        followup_date = request.POST.get("scheduled_time")
        notes = request.POST.get("notes")

        if followup_date:
            CalendarEvent.objects.create(
                employee=request.user.employee,
                title=f"Follow-up: {prospect.name}",
                scheduled_time=followup_date,
                type="follow_up",
                notes=notes,
                related_prospect=prospect,
            )
            messages.success(request, f"Follow-up added for {prospect.name}")
            return redirect("clients:callingworkspace", list_id=prospect.calling_list.id)
        else:
            messages.error(request, "Follow-up date is required.")

    return render(request, "calling/add_followup.html", {"prospect": prospect})

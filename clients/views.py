# clients/views.py
"""Views for clients app.

This file was cleaned: imports consolidated and duplicates removed. Function bodies
are left intact. If any NameError appears after this change, add back the specific
import near the top.
"""
import json
from datetime import date, datetime, timedelta
from calendar import month_name
import csv
import io

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django import forms
from django.db.models import Sum, Value, Q
from django.core.paginator import Paginator
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.urls import reverse
from django.db import transaction
from django.http import HttpResponseForbidden

# Local imports
from .models import (
    Client,
    Sale,
    CalendarEvent,
    Employee,
    Target,
    MonthlyTargetHistory,
    CallRecord,
    CallingList,
    Prospect,
    MessageTemplate,
    IncentiveRule,
    Redemption,
    Notification,
)
from .forms import SaleForm, AdminSaleForm, EditSaleForm
from django.db.models import Count, Avg, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncYear
from django.core.exceptions import FieldError
from django.views.decorators.csrf import csrf_protect


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
    # Prepare filter defaults: per-product yes/no and amount ranges
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

    # source: clients | prospects | both
    source = request.GET.get('source', 'clients')

    # base queryset for clients or prospects
    qs = None
    if source == 'prospects':
        qs = Prospect.objects.select_related('assigned_to').all()
    else:
        # default: clients
        qs = Client.objects.all()

    # Restrict view based on user role: employees should only see mapped clients/prospects
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

    # mapped filter (applies differently for prospects)
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
    # default to a smaller page size for better UX with large lists
    per_page = int(request.GET.get('per_page', 25))
    # Order by created_at where available; prospects and clients both have created_at
    order_expr = '-created_at'
    paginator = Paginator(qs.order_by(order_expr), per_page)
    page_obj = paginator.get_page(page)

    # preserve other query params for pagination links
    params = request.GET.copy()
    if 'page' in params:
        params.pop('page')
    qs_params = params.urlencode()

    # Handle POST actions: export CSV or create calling list
    # permission for creating lists
    # allow employees to create lists for their own mapped clients/prospects; admins/managers/superuser can create and assign broadly
    can_create = False
    if request.user.is_superuser:
        can_create = True
    elif hasattr(request.user, 'employee'):
        # any mapped employee can create lists (with restrictions for 'employee' role)
        can_create = True

    if request.method == 'POST':
        action = request.POST.get('action')
        selected_ids = request.POST.getlist('selected_ids')
        if action == 'export_csv':
            # export CSV of current page or selected ids
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
            # support single assign_employee or multiple assign_employee_multiple
            assign_emp_single = request.POST.get('assign_employee')
            assign_emp_multiple = request.POST.getlist('assign_employee_multiple')
            csv_file = request.FILES.get('csv_file')

            # Build selection
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

                # handle CSV upload: expect columns name, phone, email (optional)
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
                        # ignore file parse errors but surface a message
                        messages.warning(request, 'Uploaded CSV could not be parsed; skipping CSV rows.')

                # Duplicate selected prospects or clients
                if source == 'prospects':
                    for idx, sp in enumerate(selected_qs):
                        # create a new Prospect record copying key fields
                        assigned = None
                        p = Prospect(calling_list=cl, assigned_to=sp.assigned_to, name=sp.name, phone=sp.phone or '', email=sp.email or '', notes=sp.notes)
                        prospects_to_create.append(p)
                else:
                    for c in selected_qs:
                        p = Prospect(calling_list=cl, assigned_to=None, name=c.name, phone=c.phone or '', email=c.email or '')
                        prospects_to_create.append(p)

                # Bulk create prospects
                Prospect.objects.bulk_create(prospects_to_create)

                # Assignment logic
                # For normal employees (role == 'employee'), force assignment to themselves
                user_emp = getattr(request.user, 'employee', None)
                if user_emp and getattr(user_emp, 'role', None) == 'employee':
                    Prospect.objects.filter(calling_list=cl).update(assigned_to=user_emp)
                else:
                    # Admins/managers may assign to multiple employees (round-robin) or a single employee
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
        # naive approach: pick first per client
        seen = set()
        for s in last_qs:
            if s.client_id in seen:
                continue
            last_sales[s.client_id] = {'date': s.date, 'product': s.product}
            seen.add(s.client_id)
        # attach last sale attributes to client objects on this page for easier template rendering
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
        'employees': Employee.objects.select_related('user').all(),
        'products': [c[0] for c in Sale.PRODUCT_CHOICES],
        'mapped_emp': mapped_emp or '',
        'unmapped_only': bool(unmapped_only),
        'source': source,
        'can_create': can_create,
        'last_sales': last_sales,
        'per_page': per_page,
        'qs_params': qs_params,
    }
    # flag for template: is current user a plain employee
    is_employee = False
    if hasattr(request.user, 'employee') and getattr(request.user.employee, 'role', None) == 'employee':
        is_employee = True
    context['is_employee'] = is_employee

    return render(request, 'calling/list_generator.html', context)


@login_required
def employee_performance(request):
    """Employee performance overview (MVP).

    - If ?employee_id= is provided in GET and the user is admin, show that employee.
    - Otherwise, show metrics for the logged-in employee.
    """
    # determine employee and permission for selector
    emp_id = request.GET.get('employee_id')
    is_manager = False
    if hasattr(request.user, 'employee') and request.user.employee.role in ('admin', 'manager'):
        is_manager = True
    if request.user.is_superuser:
        is_manager = True

    if emp_id and is_manager:
        employee = get_object_or_404(Employee, id=emp_id)
    elif hasattr(request.user, 'employee'):
        employee = request.user.employee
    else:
        messages.error(request, 'No employee selected and you are not mapped to an employee.')
        return redirect('clients:admin_dashboard')

    # date range
    try:
        start_str = request.GET.get('start')
        end_str = request.GET.get('end')
        if start_str:
            start = datetime.fromisoformat(start_str).date()
        else:
            start = date.today() - timedelta(days=30)
        if end_str:
            end = datetime.fromisoformat(end_str).date()
        else:
            end = date.today()
    except Exception:
        start = date.today() - timedelta(days=30)
        end = date.today()

    # Sales aggregates
    sales_qs = Sale.objects.filter(employee=employee, date__range=(start, end))
    total_sales = sales_qs.count()
    # some installs use `amount`, some may use `total_amount` — try preferred field safely
    try:
        total_amount = sales_qs.aggregate(total=Sum('amount'))['total'] or 0
    except FieldError:
        try:
            total_amount = sales_qs.aggregate(total=Sum('total_amount'))['total'] or 0
        except FieldError:
            total_amount = 0
    points = sales_qs.aggregate(total=Sum('points'))['total'] or 0

    # Calls aggregates
    calls_qs = CallRecord.objects.filter(employee=employee, call_time__date__range=(start, end))
    calls_made = calls_qs.count()
    connects = calls_qs.filter(status__in=['connected', 'success']).count() if calls_made else 0
    connect_rate = (connects / calls_made * 100) if calls_made else 0

    conversion_rate = (total_sales / calls_made * 100) if calls_made else 0

    # Time series: build daily series between start and end
    days = []
    sales_series = []
    calls_series = []
    current = start
    while current <= end:
        days.append(current.strftime('%Y-%m-%d'))
        sales_series.append(sales_qs.filter(date=current).aggregate(cnt=Count('id'))['cnt'] or 0)
        calls_series.append(calls_qs.filter(call_time__date=current).aggregate(cnt=Count('id'))['cnt'] or 0)
        current += timedelta(days=1)

    # Recent sales table (top 10 recent)
    recent_sales = sales_qs.order_by('-date')[:10]

    # Export CSV if requested
    if request.GET.get('export') == 'csv':
        # build CSV response for sales_qs
        import csv as _csv
        from django.http import HttpResponse

        resp = HttpResponse(content_type='text/csv')
        filename = f"employee_{employee.id}_performance_{start}_{end}.csv"
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = _csv.writer(resp)
        writer.writerow(['date', 'client', 'amount', 'points', 'product'])
        for s in sales_qs.order_by('date'):
            client_name = s.client.name if getattr(s, 'client', None) else ''
            amount = getattr(s, 'total_amount', None) or getattr(s, 'amount', None) or ''
            points_v = getattr(s, 'points', '')
            product = getattr(s, 'product', '')
            writer.writerow([s.date, client_name, amount, points_v, product])
        return resp

    context = {
        'employee': employee,
        'start': start,
        'end': end,
        'total_sales': total_sales,
        'total_amount': total_amount,
        'points': points,
        'calls_made': calls_made,
        'connects': connects,
        'connect_rate': round(connect_rate, 1),
        'conversion_rate': round(conversion_rate, 1),
        'days': days,
        'sales_series': sales_series,
        'calls_series': calls_series,
        'recent_sales': recent_sales,
        'is_manager': is_manager,
    }

    if is_manager:
        context['employees'] = Employee.objects.select_related('user').all()

    return render(request, 'sales/employee_performance.html', context)


@login_required
def net_business(request):
    """Net business dashboard: shows sales minus redemptions/SIP stoppage.

    Filters:
    - start, end (ISO dates)
    - granularity: day|month|year
    - product (optional)

    Also supports adding a Redemption entry via POST (managers only).
    """
    # permission: only admin/manager or superuser
    if not (request.user.is_superuser or (hasattr(request.user, 'employee') and request.user.employee.role in ('admin', 'manager'))):
        messages.error(request, 'You do not have permission to view Net Business.')
        return redirect('clients:admin_dashboard')

    # handle POST to add redemption
    if request.method == 'POST':
        product = request.POST.get('product')
        entry_type = request.POST.get('entry_type', 'redemption')
        amount = request.POST.get('amount')
        date_str = request.POST.get('date')
        note = request.POST.get('note', '')
        try:
            amt = float(amount)
            d = date.fromisoformat(date_str) if date_str else date.today()
            Redemption.objects.create(product=product, entry_type=entry_type, amount=amt, date=d, note=note, created_by=request.user)
            messages.success(request, 'Redemption entry added')
            return redirect('clients:net_business')
        except Exception as e:
            messages.error(request, f'Invalid input: {e}')

    # Filters
    try:
        start_str = request.GET.get('start')
        end_str = request.GET.get('end')
        if start_str:
            start = date.fromisoformat(start_str)
        else:
            start = date(date.today().year, 1, 1)
        if end_str:
            end = date.fromisoformat(end_str)
        else:
            end = date.today()
    except Exception:
        start = date(date.today().year, 1, 1)
        end = date.today()

    gran = request.GET.get('granularity', 'month')  # day|month|year
    product_filter = request.GET.get('product')

    # Base sales queryset
    sales_qs = Sale.objects.filter(date__range=(start, end))
    if product_filter:
        sales_qs = sales_qs.filter(product=product_filter)

    # Base redemptions
    red_qs = Redemption.objects.filter(date__range=(start, end))
    if product_filter:
        red_qs = red_qs.filter(product=product_filter)

    # Grouping
    if gran == 'day':
        sales_grouped = sales_qs.annotate(period=TruncDay('date')).values('period', 'product').annotate(total=Sum('amount'), count=Count('id')).order_by('period')
        red_grouped = red_qs.annotate(period=TruncDay('date')).values('period', 'product').annotate(total=Sum('amount')).order_by('period')
    elif gran == 'year':
        sales_grouped = sales_qs.annotate(period=TruncYear('date')).values('period', 'product').annotate(total=Sum('amount'), count=Count('id')).order_by('period')
        red_grouped = red_qs.annotate(period=TruncYear('date')).values('period', 'product').annotate(total=Sum('amount')).order_by('period')
    else:
        sales_grouped = sales_qs.annotate(period=TruncMonth('date')).values('period', 'product').annotate(total=Sum('amount'), count=Count('id')).order_by('period')
        red_grouped = red_qs.annotate(period=TruncMonth('date')).values('period', 'product').annotate(total=Sum('amount')).order_by('period')

    # Build a mapping: (period, product) -> totals
    data = {}
    products = set()
    for r in sales_grouped:
        key = (r['period'].date() if hasattr(r['period'], 'date') else r['period'], r['product'])
        data.setdefault(key, {'sales': 0, 'redemptions': 0})
        data[key]['sales'] = float(r['total'] or 0)
        # include transaction count (number of sales) for this period+product
        try:
            data[key]['count'] = int(r.get('count', 0) or 0)
        except Exception:
            data[key]['count'] = 0
        products.add(r['product'])

    for r in red_grouped:
        key = (r['period'].date() if hasattr(r['period'], 'date') else r['period'], r['product'])
        data.setdefault(key, {'sales': 0, 'redemptions': 0})
        data[key]['redemptions'] = float(r['total'] or 0)
        products.add(r['product'])

    # Prepare rows sorted by period
    rows = []
    # get sorted unique periods
    periods = sorted({k[0] for k in data.keys()})
    for p in periods:
        for prod in sorted(products):
            s = data.get((p, prod), {'sales': 0, 'redemptions': 0})
            net = s['sales'] - s['redemptions']
            rows.append({'period': p, 'product': prod, 'sales': s['sales'], 'redemptions': s['redemptions'], 'net': net})

    # totals by product
    totals = {}
    for prod in products:
        total_sales = sum(r['sales'] for r in rows if r['product'] == prod)
        total_red = sum(r['redemptions'] for r in rows if r['product'] == prod)
        totals[prod] = {'sales': total_sales, 'redemptions': total_red, 'net': total_sales - total_red}

    # Compute counts per product (number of sales transactions) in one query
    counts_qs = sales_qs.values('product').annotate(cnt=Count('id'))
    counts_map = {c['product']: int(c['cnt'] or 0) for c in counts_qs}
    for prod in totals.keys():
        totals[prod]['count'] = counts_map.get(prod, 0)

    # expose string versions of dates so HTML date inputs retain values
    start_str = start.isoformat() if hasattr(start, 'isoformat') else str(start)
    end_str = end.isoformat() if hasattr(end, 'isoformat') else str(end)

    context = {
        'rows': rows,
        'totals': totals,
        'start': start,
        'end': end,
        'start_str': start_str,
        'end_str': end_str,
        'granularity': gran,
        'products': [c[0] for c in Sale.PRODUCT_CHOICES],
        'selected_product': product_filter,
    }

    return render(request, 'dashboards/net_business.html', context)

def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)

            # 🔥 Auto-recalc points for this user
            if hasattr(user, "employee"):
                if user.employee.role == "admin":
                    sales = Sale.objects.all()
                else:
                    sales = Sale.objects.filter(employee=user.employee)
                for s in sales:
                    s.compute_points()
                    s.save()

            # ✅ Redirect based on role
            if hasattr(user, "employee"):
                role = user.employee.role.lower()
                if role == "admin":
                    return redirect("clients:admin_dashboard")   # fixed
                elif role == "employee":
                    return redirect("clients:employee_dashboard")  # fixed
            else:
                messages.error(request, "No employee role mapped.")
        else:
            messages.error(request, "Invalid username or password")

    # ✅ Always return a response on GET or failed POST
    return render(request, "login.html")



@login_required
def logout_view(request):
    logout(request)
    return redirect("clients:login")

# Client Form
class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "email", "phone", "pan", "address", "mapped_to",
                  "sip_status", "sip_amount", "sip_topup",
                  "health_status", "health_cover", "health_topup", "health_product",
                  "life_status", "life_cover", "life_product",
                  "motor_status", "motor_insured_value", "motor_product",
                  "pms_status", "pms_amount", "pms_start_date"]

from django.db.models import Sum, Value
from django.db.models.functions import Coalesce

from django.db.models import Sum
from django.utils.timezone import now
from datetime import datetime

from django.db.models import Sum
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Sale, Client

@login_required
@login_required
def admin_dashboard(request):
    today = timezone.now().date()
    month = today.month
    year = today.year

    # All-time sales (if you use it elsewhere)
    all_sales_qs = Sale.objects.all()

    # --- IMPORTANT: use month-scoped queryset for 'This Month' widgets ---
    monthly_sales_qs = Sale.objects.filter(created_at__year=year, created_at__month=month)

    # ---------------- Overall summary ----------------
    total_clients = Client.objects.count()
    # If "Total Sales (This Month)" is what you want in the card, use monthly_sales_qs
    total_sales = monthly_sales_qs.aggregate(total=Sum("amount"))["total"] or 0
    total_points = monthly_sales_qs.aggregate(total=Sum("points"))["total"] or 0

    # ---------------- Product-wise totals (THIS MONTH) ----------------
    sip_sales = monthly_sales_qs.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = monthly_sales_qs.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = monthly_sales_qs.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = monthly_sales_qs.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = monthly_sales_qs.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = monthly_sales_qs.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    # ---------------- Section 1: Today's Summary ----------------
    todays_summary = []
    for emp in Employee.objects.all():
        emp_sales = monthly_sales_qs.filter(employee=emp, created_at__date=today)
        todays_summary.append({
            "employee": emp.user.username if hasattr(emp, "user") else emp.name,
            "sales": emp_sales.aggregate(total=Sum("amount"))["total"] or 0,
            "points": emp_sales.aggregate(total=Sum("points"))["total"] or 0,
            "new_clients": Client.objects.filter(mapped_to=emp, created_at__date=today).count(),
        })

    # ---------------- Section 2: Monthly Progress (Employee × Product) ----------------
    PRODUCT_MAP = {
        "SIP": "sip",
        "Lumsum": "lumpsum",
        "Life Insurance": "life",
        "Health Insurance": "health",
        "Motor Insurance": "motor",
        "PMS": "pms",
    }

    monthly_progress = []
    for emp in Employee.objects.all():
        row = {"employee": emp.user.username if hasattr(emp, "user") else emp.name}
        total_emp_sales = 0
        for product, key in PRODUCT_MAP.items():
            amt = monthly_sales_qs.filter(
                employee=emp,
                product=product,
            ).aggregate(total=Sum("amount"))["total"] or 0
            row[key] = amt
            total_emp_sales += amt
        row["total"] = total_emp_sales
        monthly_progress.append(row)

    # ---------------- Section 3: Monthly Cumulative Summary ----------------
    monthly_summary = {
        "total_clients": Client.objects.filter(created_at__year=year, created_at__month=month).count(),
        "total_sales": total_sales,
        "total_points": total_points,
        "sip": sip_sales,
        "lumpsum": lumsum_sales,
        "life": life_sales,
        "health": health_sales,
        "motor": motor_sales,
        "pms": pms_sales,
    }

    # ---------------- Notifications ----------------
    notifications = []
    unread_notifications = 0
    if request.user.is_authenticated:
        notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:10]
        unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False).count()

    # ---------------- Context ----------------
    context = {
        "total_clients": total_clients,
        "total_sales": total_sales,
        "total_points": total_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "todays_summary": todays_summary,
        "monthly_progress": monthly_progress,
        "monthly_summary": monthly_summary,
        "notifications": notifications,
        "unread_notifications": unread_notifications,
    }

    return render(request, "dashboards/admin_dashboard.html", context)


@login_required
def employee_dashboard(request):
    emp = request.user.employee
    today = now().date()

    # --- Querysets restricted by time ---
    monthly_sales_qs = Sale.objects.filter(
        employee=emp,
        date__year=today.year,
        date__month=today.month
    )
    today_sales_qs = monthly_sales_qs.filter(date=today)

    # --- Totals for this employee (THIS MONTH only) ---
    total_sales = monthly_sales_qs.aggregate(total=Sum("amount"))["total"] or 0
    total_points = monthly_sales_qs.aggregate(total=Sum("points"))["total"] or 0

    # --- Product-wise totals (THIS MONTH only) ---
    sip_sales = monthly_sales_qs.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    lumsum_sales = monthly_sales_qs.filter(product="Lumsum").aggregate(total=Sum("amount"))["total"] or 0
    life_sales = monthly_sales_qs.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    health_sales = monthly_sales_qs.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    motor_sales = monthly_sales_qs.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    pms_sales = monthly_sales_qs.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    # --- Today's sales (resets daily) ---
    today_sales = today_sales_qs.values("product").annotate(total=Sum("amount"))
    today_sales_dict = {s["product"]: s["total"] for s in today_sales}
    today = timezone.now().date()
    todays_tasks = CalendarEvent.objects.filter(
    employee=request.user.employee,
    scheduled_time__date=today,
    ).order_by("scheduled_time")

    # --- Monthly sales (resets monthly) ---
    month_sales = monthly_sales_qs.values("product").annotate(total=Sum("amount"))
    month_sales_dict = {s["product"]: s["total"] for s in month_sales}

    # --- Global targets ---
    daily_targets = Target.objects.filter(target_type="daily")
    monthly_targets = Target.objects.filter(target_type="monthly")

    # --- Attach progress to each target ---
    for target in daily_targets:
        achieved = today_sales_dict.get(target.product, 0)
        target.achieved = achieved
        target.progress = (achieved / target.target_value * 100) if target.target_value else 0

    for target in monthly_targets:
        achieved = month_sales_dict.get(target.product, 0)
        target.achieved = achieved
        target.progress = (achieved / target.target_value * 100) if target.target_value else 0

    # --- Past 6 months performance history ---
    history = MonthlyTargetHistory.objects.filter(employee=emp).order_by("-year", "-month")[:6]

    
    todays_events = CalendarEvent.objects.filter(
    employee=request.user.employee,
    scheduled_time__date=today,
    status="pending",   # 👈 only show pending
).order_by("scheduled_time")

    context = {
        "total_sales": total_sales,
        "total_points": total_points,
        "sip_sales": sip_sales,
        "lumsum_sales": lumsum_sales,
        "life_sales": life_sales,
        "todays_events": todays_events,
        "health_sales": health_sales,
        "motor_sales": motor_sales,
        "pms_sales": pms_sales,
        "today_sales_dict": today_sales_dict,
        "month_sales_dict": month_sales_dict,
        "daily_targets": daily_targets,
        "monthly_targets": monthly_targets,
        "history": history,   # 👈 important for template
    }
    return render(request, "dashboards/employee_dashboard.html", context)


@login_required
def add_sale(request):
    # allow choosing employee when creating a sale. use AdminSaleForm which includes `employee` field
    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)

            # prefer the employee chosen in the form, otherwise default to logged-in employee
            chosen_emp = form.cleaned_data.get("employee")
            if chosen_emp:
                sale.employee = chosen_emp
            else:
                sale.employee = getattr(request.user, "employee", None)

            # ensure client is set (form should populate it via the hidden client field)
            if not sale.client:
                client_id = request.POST.get("client")
                if not client_id:
                    messages.error(request, "Please select a client from search results.")
                    # pass employees so template can re-render selector
                    return render(request, "sales/add_sale.html", {"form": form, "employees": Employee.objects.select_related('user').all(), "current_employee_id": getattr(request.user, 'employee').id if hasattr(request.user, 'employee') else None})
                try:
                    sale.client = Client.objects.get(id=client_id)
                except Client.DoesNotExist:
                    messages.error(request, "Selected client does not exist.")
                    return render(request, "sales/add_sale.html", {"form": form, "employees": Employee.objects.select_related('user').all(), "current_employee_id": getattr(request.user, 'employee').id if hasattr(request.user, 'employee') else None})

            # compute points before saving
            sale.compute_points()
            sale.save()

            messages.success(request, "Sale added successfully!")
            return redirect("clients:all_sales")
    else:
        # default to admin-capable form so employee selector is available
        initial = {}
        if hasattr(request.user, 'employee'):
            initial['employee'] = request.user.employee.id
            try:
                initial['date'] = date.today()
            except Exception:
                pass
        form = AdminSaleForm(initial=initial)

    # provide employees list and current employee id for template selector
    employees_qs = Employee.objects.select_related('user').all()
    current_emp_id = getattr(request.user, 'employee').id if hasattr(request.user, 'employee') else None

    return render(request, "sales/add_sale.html", {"form": form, "employees": employees_qs, "current_employee_id": current_emp_id})
# clients/views.py
from django.db.models import Q
from .models import MessageTemplate   # (add this import at top if not already)

# pagination settings
PER_PAGE = 50  # change to how many clients you want per page


@login_required
def all_clients(request):
    clients_qs = Client.objects.select_related("mapped_to").order_by('id')

    # Filters
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

    if sip_min:
        try:
            clients_qs = clients_qs.filter(sip_amount__gte=float(sip_min))
        except ValueError:
            pass
    if sip_max:
        try:
            clients_qs = clients_qs.filter(sip_amount__lte=float(sip_max))
        except ValueError:
            pass
    if pms_min:
        try:
            clients_qs = clients_qs.filter(pms_amount__gte=float(pms_min))
        except ValueError:
            pass
    if pms_max:
        try:
            clients_qs = clients_qs.filter(pms_amount__lte=float(pms_max))
        except ValueError:
            pass
    if life_min:
        try:
            clients_qs = clients_qs.filter(life_cover__gte=float(life_min))
        except ValueError:
            pass
    if life_max:
        try:
            clients_qs = clients_qs.filter(life_cover__lte=float(life_max))
        except ValueError:
            pass
    if health_min:
        try:
            clients_qs = clients_qs.filter(health_cover__gte=float(health_min))
        except ValueError:
            pass
    if health_max:
        try:
            clients_qs = clients_qs.filter(health_cover__lte=float(health_max))
        except ValueError:
            pass

    # Paginate
    paginator = Paginator(clients_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

    # Compact page range
    current = page_obj.number
    total_pages = paginator.num_pages
    start = current - 3 if (current - 3) > 1 else 1
    end = current + 3 if (current + 3) < total_pages else total_pages
    page_range = range(start, end + 1)

    # Build base_qs WITHOUT 'page' so we don't duplicate page param in links
    get_params = request.GET.copy()
    if 'page' in get_params:
        del get_params['page']
    base_qs = get_params.urlencode()  # '' if no other params

    context = {
        "clients_page": page_obj,
        "page_range": page_range,
        "total_pages": total_pages,
        # keep filters in context if you want to use them individually
        "sip_status": sip_status, "pms_status": pms_status,
        "life_status": life_status, "health_status": health_status,
        "sip_min": sip_min, "sip_max": sip_max,
        "pms_min": pms_min, "pms_max": pms_max,
        "life_min": life_min, "life_max": life_max,
        "health_min": health_min, "health_max": health_max,
        "base_qs": base_qs,
    }
    templates = MessageTemplate.objects.all()     # fetch all message templates
    context.update({"templates": templates})   
    return render(request, "clients/all_clients.html", context)

from django.conf import settings
from .models import MessageTemplate 
PER_PAGE = getattr(settings, "PER_PAGE", 50)

@login_required
def my_clients(request):
    # Base queryset: clients mapped to the logged-in user's employee record
    # adapt `mapped_to=request.user.employee` if your relation differs
    clients_qs = Client.objects.filter(mapped_to=request.user.employee).order_by('id')

    # --- Filters from GET ---
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

    if sip_min:
        try:
            clients_qs = clients_qs.filter(sip_amount__gte=float(sip_min))
        except ValueError:
            pass
    if sip_max:
        try:
            clients_qs = clients_qs.filter(sip_amount__lte=float(sip_max))
        except ValueError:
            pass
    if pms_min:
        try:
            clients_qs = clients_qs.filter(pms_amount__gte=float(pms_min))
        except ValueError:
            pass
    if pms_max:
        try:
            clients_qs = clients_qs.filter(pms_amount__lte=float(pms_max))
        except ValueError:
            pass
    if life_min:
        try:
            clients_qs = clients_qs.filter(life_cover__gte=float(life_min))
        except ValueError:
            pass
    if life_max:
        try:
            clients_qs = clients_qs.filter(life_cover__lte=float(life_max))
        except ValueError:
            pass
    if health_min:
        try:
            clients_qs = clients_qs.filter(health_cover__gte=float(health_min))
        except ValueError:
            pass
    if health_max:
        try:
            clients_qs = clients_qs.filter(health_cover__lte=float(health_max))
        except ValueError:
            pass

    # --- Pagination ---
    paginator = Paginator(clients_qs, PER_PAGE)
    page_num = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(page_num)
    except Exception:
        page_obj = paginator.get_page(1)

    current = page_obj.number
    total_pages = paginator.num_pages
    start = current - 3 if (current - 3) > 1 else 1
    end = current + 3 if (current + 3) < total_pages else total_pages
    page_range = range(start, end + 1)

    # --- build base querystring without 'page' so links are clean ---
    get_params = request.GET.copy()
    if 'page' in get_params:
        del get_params['page']
    base_qs = get_params.urlencode()  # empty string if no params
      # (add this import at top if not already)


    templates = MessageTemplate.objects.all()     # fetch all message templates
        # make them available in template
    context = {
        "clients_page": page_obj,
        "page_range": page_range,
        "total_pages": total_pages,
        "base_qs": base_qs,
    }
    context.update({"templates": templates})  
    return render(request, "clients/my_clients.html", context)

@login_required
def add_client(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Client added successfully!")
            return redirect("clients:all_clients")  # ✅ namespaced
    else:
        form = ClientForm()
    return render(request, "clients/add_client.html", {"form": form})

from django.db.models import Q

from django.core.paginator import Paginator

@login_required
def all_sales(request):
    """
    List all sales with product/client/employee/start_date/end_date filters.
    Pagination preserves current GET filters via `qstring`.
    """
    sales_qs = Sale.objects.all().order_by("-date", "-created_at")

    # Role-based filtering: employees only see their own sales
    if hasattr(request.user, "employee") and request.user.employee.role == "employee":
        sales_qs = sales_qs.filter(employee=request.user.employee)

    # Filters from GET
    product = request.GET.get("product")
    client = request.GET.get("client")
    employee = request.GET.get("employee")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if product:
        sales_qs = sales_qs.filter(product=product)
    if client:
        # allow passing client id
        try:
            cid = int(client)
            sales_qs = sales_qs.filter(client_id=cid)
        except Exception:
            # fallback to name search (if you want)
            sales_qs = sales_qs.filter(client__name__icontains=client)
    if employee:
        sales_qs = sales_qs.filter(employee__user__username__icontains=employee)
    if start_date and end_date:
        sales_qs = sales_qs.filter(date__range=[start_date, end_date])

    # Default: show only today's sales unless any filter is present
    if not (product or client or employee or start_date or end_date):
        sales_qs = sales_qs.filter(date=date.today())

    # Pagination
    paginator = Paginator(sales_qs, 50)  # 10 records per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Build querystring of current GET params EXCEPT 'page'
    qdict = request.GET.copy()
    qdict.pop("page", None)
    qstring = qdict.urlencode()  # empty string if no other params

    context = {
        "sales": page_obj,  # page object used by template
        "is_employee": hasattr(request.user, "employee") and request.user.employee.role == "employee",
        "qstring": qstring,
    }
    return render(request, "sales/all_sales.html", context)

@login_required
def admin_add_sale(request):
    if not request.user.is_superuser and request.user.employee.role != "admin":
        return redirect("clients:employee_dashboard")  # block non-admins

    if request.method == "POST":
        form = AdminSaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)

            # 🔹 Ensure employee is assigned
            if not sale.employee_id:
                form.add_error("employee", "Please select an employee for this sale.")
            else:
                sale.save()
                messages.success(request, "Sale added successfully!")
                return redirect("clients:all_sales")  # show latest sales immediately
    else:
        form = AdminSaleForm()

    return render(request, "sales/admin_add_sale.html", {"form": form})





from .models import IncentiveRule

@login_required
def manage_incentive_rules(request):
    # Role check OR hardcoded pass
    if request.user.employee.role != "admin" and request.GET.get("pass") != "SuperSecret123":
        messages.error(request, "You do not have permission to access incentive rules.")
        return redirect("clients:admin_dashboard")

    rules = IncentiveRule.objects.all()

    if request.method == "POST":
        for rule in rules:
            unit_field = f"unit_{rule.id}"
            points_field = f"points_{rule.id}"
            if unit_field in request.POST and points_field in request.POST:
                rule.unit_amount = request.POST[unit_field]
                rule.points_per_unit = request.POST[points_field]
                rule.save()
        messages.success(request, "Incentive rules updated successfully!")
        return redirect("clients:manage_incentive_rules")

    return render(request, "incentives/manage_rules.html", {"rules": rules})


@login_required
def recalc_points(request):
    # Admin sees all sales, Employee sees only their own
    if request.user.employee.role == "admin":
        sales = Sale.objects.all()
    else:
        sales = Sale.objects.filter(employee=request.user.employee)

    count = 0
    for s in sales:
        s.compute_points()   # use IncentiveRule
        s.save()
        count += 1

    messages.success(request, f"Recalculated points for {count} sales.")
    if request.user.employee.role == "admin":
        return redirect("clients:all_sales")
    else:
        return redirect("clients:employee_dashboard")




from django.db.models import Q
from django.http import JsonResponse

@login_required
def search_clients(request):
    query = request.GET.get("q", "")
    clients = Client.objects.filter(
        Q(name__icontains=query) |
        Q(email__icontains=query) |
        Q(phone__icontains=query)
    )[:10]

    results = [
        {"id": c.id, "text": f"{c.name} ({c.email or ''} {c.phone or ''})"}
        for c in clients
    ]
    return JsonResponse({"results": results})


from decimal import Decimal

@login_required
def edit_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)

    if request.method == "POST":
        form = EditSaleForm(request.POST, instance=sale)
        if form.is_valid():
            form.save()
            messages.success(request, "Sale updated successfully!")
            return redirect("clients:all_sales")
    else:
        form = EditSaleForm(instance=sale)

    return render(request, "sales/edit_sale.html", {"form": form, "sale": sale})



@login_required
def delete_sale(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    if request.method == "POST":
        sale.delete()
        messages.success(request, "Sale deleted successfully!")
        return redirect("clients:admin_dashboard")
    return render(request, "sales/delete_sale.html", {"sale": sale})


def save_model(self, request, obj, form, change):
    if not request.user.is_superuser:
        emp = getattr(request.user, "employee", None)
        if emp:
            obj.employee = emp
    super().save_model(request, obj, form, change)

    # 🔹 Update client status based on this sale
    client = obj.client
    if obj.product == "SIP":
        client.sip_status = True
        client.sip_amount = (client.sip_amount or 0) + obj.amount
    elif obj.product == "Lumsum":
        # You may want to track lumpsum in SIP or separately
        pass
    elif obj.product == "Life Insurance":
        client.life_status = True
        client.life_cover = (client.life_cover or 0) + obj.amount
    elif obj.product == "Health Insurance":
        client.health_status = True
        client.health_cover = (client.health_cover or 0) + obj.amount
    elif obj.product == "Motor Insurance":
        client.motor_status = True
        client.motor_insured_value = (client.motor_insured_value or 0) + obj.amount
    elif obj.product == "PMS":
        client.pms_status = True
        client.pms_amount = (client.pms_amount or 0) + obj.amount
        if not client.pms_start_date:
            client.pms_start_date = obj.date

    client.save()

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

@receiver([post_save, post_delete], sender=Sale)
def update_client_status(sender, instance, **kwargs):
    client = instance.client
    # Recalculate totals from all sales
    sales = Sale.objects.filter(client=client)

    client.sip_amount = sales.filter(product="SIP").aggregate(total=Sum("amount"))["total"] or 0
    client.life_cover = sales.filter(product="Life Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.health_cover = sales.filter(product="Health Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.motor_insured_value = sales.filter(product="Motor Insurance").aggregate(total=Sum("amount"))["total"] or 0
    client.pms_amount = sales.filter(product="PMS").aggregate(total=Sum("amount"))["total"] or 0

    client.sip_status = client.sip_amount > 0
    client.life_status = client.life_cover > 0
    client.health_status = client.health_cover > 0
    client.motor_status = client.motor_insured_value > 0
    client.pms_status = client.pms_amount > 0

    client.save()


@login_required
def client_analysis(request):
    # Base queryset
    if request.user.employee.role == "admin":
        clients = Client.objects.all()
    else:
        clients = Client.objects.filter(mapped_to=request.user.employee)

    # Apply filters
    filters = {}
    if request.GET.get("sip_status") in ["yes", "no"]:
        filters["sip_status"] = True if request.GET["sip_status"] == "yes" else False
    if request.GET.get("life_status") in ["yes", "no"]:
        filters["life_status"] = True if request.GET["life_status"] == "yes" else False
    if request.GET.get("health_status") in ["yes", "no"]:
        filters["health_status"] = True if request.GET["health_status"] == "yes" else False
    if request.GET.get("motor_status") in ["yes", "no"]:
        filters["motor_status"] = True if request.GET["motor_status"] == "yes" else False
    if request.GET.get("pms_status") in ["yes", "no"]:
        filters["pms_status"] = True if request.GET["pms_status"] == "yes" else False

    clients = clients.filter(**filters)

    # Date filter
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    if start_date and end_date:
        clients = clients.filter(created_at__range=[start_date, end_date])

    # Export
    if "export" in request.GET:
        import csv
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="clients_analysis.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Name", "Email", "Phone", "SIP", "Life", "Health", "Motor", "PMS", "Created At"])
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


# clients/views.py
from django.contrib.auth.decorators import user_passes_test

def is_admin(user):
    return hasattr(user, "employee") and user.employee.role == "admin"

@user_passes_test(is_admin)
def map_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)
    employees = Employee.objects.all()

    if request.method == "POST":
        emp_id = request.POST.get("employee")
        if emp_id:
            employee = get_object_or_404(Employee, id=emp_id)
            client.mapped_to = employee
            client.status = "Mapped"
            client.save()
            messages.success(request, f"Client {client.name} mapped to {employee.user.username}")
        else:
            client.mapped_to = None
            client.status = "Unmapped"
            client.save()
            messages.success(request, f"Client {client.name} unmapped")

        return redirect("clients:all_clients")

    return render(request, "clients/map_client.html", {"client": client, "employees": employees})




def _last_n_months(today, n=12):
    """Return list of (year, month) tuples from oldest -> newest (n months including current)."""
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append((y, m))
        if m == 1:
            m = 12
            y -= 1
        else:
            m -= 1
    months.reverse()
    return months

# Monthly target helpers
def employee_past_performance(request):
    """
    Page: shows a Chart.js line chart of monthly points (last 12 months)
    and a clickable list of months (each links to month detail).
    """
    emp = request.user.employee
    today = now().date()


    months = _last_n_months(today, n=12)
    

    labels = []
    points_data = []
    months_data = []  # list of dicts to render table rows

    for (y, m) in months:
        label = f"{month_name[m]} {y}"
        # Sum points for this employee in that month
        pts = (
            Sale.objects.filter(employee=emp, date__year=y, date__month=m)
            .aggregate(total=Sum("points"))["total"]
            or 0
        )

        labels.append(label)
        points_data.append(int(pts))
        months_data.append({"year": y, "month": m, "label": label, "points": int(pts)})

    context = {
        "labels_json": json.dumps(labels),
        "points_json": json.dumps(points_data),
        "months_data": months_data,
    }
    return render(request, "dashboards/employee_past_performance.html", context)


@login_required
def past_month_performance(request, year, month):
    """
    Shows product-wise business done for this employee in the specific month.
    product rows will include: product name, total_amount, total_points, (and monthly target & achieved if available)
    """
    emp = request.user.employee

    # product-wise sales in the month
    product_sales = (
        Sale.objects.filter(employee=emp, date__year=year, date__month=month)
        .values("product")
        .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        .order_by("-total_amount")
    )

    # Also fetch MonthlyTargetHistory rows (if you want to show target_value & achieved_value)
    target_history = MonthlyTargetHistory.objects.filter(employee=emp, year=year, month=month)
    target_map = {t.product: t for t in target_history}

    products = []
    for row in product_sales:
        prod = row["product"]
        prod_row = {
            "product": prod,
            "total_amount": row["total_amount"] or 0,
            "total_points": int(row["total_points"] or 0),
            "target_value": target_map.get(prod).target_value if prod in target_map else None,
            "achieved_value": target_map.get(prod).achieved_value if prod in target_map else None,
        }
        products.append(prod_row)

    context = {
        "year": year,
        "month": month,
        "month_label": f"{month_name[month]} {year}",
        "products": products,
    }
    return render(request, "dashboards/past_month_performance.html", context)




@login_required
def admin_past_performance(request, n_months=12):
    # allow staff OR superuser
    # if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
    #     from django.http import HttpResponseForbidden
    #     return HttpResponseForbidden("Forbidden: staff or superuser required")

    today = now().date()
    months = _last_n_months(today, n=n_months)

    labels = []
    totals_data = []
    months_data = []

    for (y, m) in months:
        label = f"{month_name[m]} {y}"

        total_points = (
            Sale.objects.filter(date__year=y, date__month=m)
            .aggregate(total=Sum("points"))["total"]
            or 0
        )
        total_amount = (
            Sale.objects.filter(date__year=y, date__month=m)
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )

        labels.append(label)
        totals_data.append(int(total_points))
        months_data.append({
            "year": y,
            "month": m,
            "label": label,
            "points": int(total_points),
            "amount": float(total_amount),
        })

    # Top performers for the most recent month:
    latest_year, latest_month = months[-1]

    # Use user first/last name fields (safe), fallback to username
    top_performers_qs = (
        Sale.objects.filter(date__year=latest_year, date__month=latest_month)
        .values(
            "employee__id",
            "employee__user__username",
            "employee__user__first_name",
            "employee__user__last_name",
        )
        .annotate(total_points=Sum("points"), total_amount=Sum("amount"))
        .order_by("-total_points")
    )

    top_performers = []
    for r in top_performers_qs:
        first = (r.get("employee__user__first_name") or "").strip()
        last = (r.get("employee__user__last_name") or "").strip()
        if first or last:
            full_name = (first + " " + last).strip()
        else:
            full_name = r.get("employee__user__username") or "Unknown"

        top_performers.append({
            "employee_id": r.get("employee__id"),
            "username": r.get("employee__user__username") or "",
            "full_name": full_name,
            "total_points": int(r.get("total_points") or 0),
            "total_amount": float(r.get("total_amount") or 0),
        })

    context = {
        "labels_json": json.dumps(labels),
        "totals_json": json.dumps(totals_data),
        "months_data": months_data,
        "top_performers": top_performers,
        "latest_month_label": months_data[-1]["label"],
        "latest_year": latest_year,
        "latest_month": latest_month,
    }

    return render(request, "dashboards/admin_past_performance.html", context)


@login_required
def admin_past_month_performance(request, year, month):
    # allow staff OR superuser
    # if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
    #     from django.http import HttpResponseForbidden
    #     return HttpResponseForbidden("Forbidden: staff or superuser required")

    # Product-wise summary
    product_sales = (
        Sale.objects.filter(date__year=year, date__month=month)
        .values("product")
        .annotate(total_amount=Sum("amount"), total_points=Sum("points"))
        .order_by("-total_amount")
    )

    # Target aggregation across employees (if any)
    target_history = MonthlyTargetHistory.objects.filter(year=year, month=month)
    target_map = {}
    if target_history.exists():
        summed_targets = (
            target_history.values("product")
            .annotate(target_value_sum=Sum("target_value"), achieved_value_sum=Sum("achieved_value"))
        )
        for t in summed_targets:
            target_map[t["product"]] = {
                "target_value": float(t["target_value_sum"] or 0),
                "achieved_value": float(t["achieved_value_sum"] or 0),
            }

    products = []
    for row in product_sales:
        prod = row["product"]
        products.append({
            "product": prod,
            "total_amount": float(row["total_amount"] or 0),
            "total_points": int(row["total_points"] or 0),
            "target_value": target_map.get(prod, {}).get("target_value"),
            "achieved_value": target_map.get(prod, {}).get("achieved_value"),
        })

    # Top performers for that month (employee -> points/amount)
    top_performers_qs = (
        Sale.objects.filter(date__year=year, date__month=month)
        .values(
            "employee__id",
            "employee__user__username",
            "employee__user__first_name",
            "employee__user__last_name",
        )
        .annotate(total_points=Sum("points"), total_amount=Sum("amount"))
        .order_by("-total_points")
    )

    top_performers = []
    for r in top_performers_qs:
        first = (r.get("employee__user__first_name") or "").strip()
        last = (r.get("employee__user__last_name") or "").strip()
        if first or last:
            full_name = (first + " " + last).strip()
        else:
            full_name = r.get("employee__user__username") or "Unknown"

        top_performers.append({
            "employee_id": r.get("employee__id"),
            "username": r.get("employee__user__username") or "",
            "full_name": full_name,
            "total_points": int(r.get("total_points") or 0),
            "total_amount": float(r.get("total_amount") or 0),
        })

    context = {
        "year": int(year),
        "month": int(month),
        "month_label": f"{month_name[int(month)]} {year}",
        "products": products,
        "top_performers": top_performers,
    }

    return render(request, "dashboards/admin_past_month_performance.html", context)


import io
from django.contrib import messages
from .models import CallingList, Prospect

@login_required
def upload_list(request):
    if request.method == "POST" and request.FILES.get("file"):
        # pandas used for Excel parsing; import locally to keep startup light
        try:
            import pandas as pd  # type: ignore[import]
        except Exception:
            pd = None
        from datetime import timedelta
        from django.utils import timezone

        file = request.FILES["file"]

        # Read rows as list of dicts; prefer pandas if available for Excel support
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
            # pandas not available: support CSV via csv.DictReader only
            if not file.name.endswith('.csv'):
                messages.error(request, 'Excel uploads require pandas. Please upload a CSV or install pandas.')
                return redirect('clients:upload_list')
            decoded = file.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(decoded))
            rows = [r for r in reader]
            columns = set(rows[0].keys()) if rows else set()

        daily_calls = int(request.POST.get("daily_calls", 5))  # default 5 if not provided

        # Respect selected employees from the form: POST 'employees[]' contains selected employee ids
        selected_emp_ids = request.POST.getlist('employees[]')
        if selected_emp_ids:
            employees = list(Employee.objects.filter(id__in=selected_emp_ids).select_related('user'))
        else:
            employees = list(Employee.objects.filter(role="employee").select_related('user'))

        # Validation: ensure we have at least one employee to assign calls to
        if not employees:
            messages.error(request, "No employees available to assign calls. Please add employees or select at least one.")
            return redirect('clients:upload_list')

        emp_count = len(employees)
        emp_index = 0

        prospect_objs = []
        # Build prospect instances in-memory for bulk_create
        for row in rows:
            assigned_to = None

            # ✅ if CSV/Excel has assigned_to column
            if 'assigned_to' in columns and row.get('assigned_to'):
                try:
                    assigned_to = Employee.objects.get(user__username=row.get('assigned_to'))
                except Employee.DoesNotExist:
                    assigned_to = None

            # ✅ if not provided → auto distribute
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

        # Use a DB transaction and bulk_create for performance
        with transaction.atomic():
            calling_list = CallingList.objects.create(
                title=request.POST.get("title", "Untitled List"),
                uploaded_by=request.user,
            )

            # attach calling_list to each prospect object and bulk insert
            for obj in prospect_objs:
                obj.calling_list = calling_list
            Prospect.objects.bulk_create(prospect_objs)

            # refresh prospects from DB (they are the ones we just created)
            prospects = list(calling_list.prospects.select_related('assigned_to').all())

            # Create calendar events in bulk
            start_date = timezone.now().date()
            # if today is Sat (5) or Sun (6), move to Monday
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

                        # skip weekends
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

    # For GET render, provide employees so UI can list/select them
    employees = Employee.objects.filter(role="employee").select_related('user')
    return render(request, "calling/upload_list.html", {"employees": employees})



@login_required
def admin_lists(request):
    # Fetch all calling lists (latest first)
    # Prefetch prospects and their assigned employees to avoid N+1 queries
    calling_lists = CallingList.objects.all().order_by("-created_at").prefetch_related('prospects__assigned_to')

    # Attach computed attributes to each CallingList for template rendering:
    # - assigned_employees_count: number of distinct employees assigned within that list
    # - assigned_employees: comma-separated display names of assigned employees (username or full name)
    # - created_by_display: username of the user who uploaded the list
    for cl in calling_lists:
        seen = set()
        names = []
        for p in cl.prospects.all():
            emp = getattr(p, 'assigned_to', None)
            if emp and emp.id not in seen:
                seen.add(emp.id)
                # prefer full name if available, otherwise username
                uname = ''
                try:
                    uname = emp.user.get_full_name() or emp.user.username
                except Exception:
                    uname = str(emp)
                names.append(uname)
        cl.assigned_employees_count = len(names)
        cl.assigned_employees = ', '.join(names) if names else ''
        # uploaded_by may be null
        if getattr(cl, 'uploaded_by', None):
            try:
                cl.created_by_display = cl.uploaded_by.get_full_name() or cl.uploaded_by.username
            except Exception:
                cl.created_by_display = str(cl.uploaded_by)
        else:
            cl.created_by_display = ''

    context = {
        "calling_lists": calling_lists,
    }
    return render(request, "calling/admin_lists.html", context)

from django.urls import reverse

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
            prospect.assigned_to = None  # unassign
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
    
    # fetch all lists that have at least one prospect assigned to this employee
    my_lists = CallingList.objects.filter(prospects__assigned_to=employee).distinct()

    # attach a count of how many prospects from each list are assigned to this employee
    for clist in my_lists:
        clist.my_prospects_count = clist.prospects.filter(assigned_to=employee).count()

    context = {
        "my_lists": my_lists
    }
    return render(request, "calling/employee_lists.html", context)


@login_required
def calling_workspace(request, list_id):
    employee = request.user.employee
    calling_list = get_object_or_404(CallingList, id=list_id)

    # only fetch prospects assigned to this employee
    prospects_qs = calling_list.prospects.filter(assigned_to=employee).order_by('id')

    # pagination for large lists
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 25))
    paginator = Paginator(prospects_qs, per_page)
    page_obj = paginator.get_page(page)

    if request.method == "POST":
        action = request.POST.get("action")
        prospect_id = request.POST.get("prospect_id")
        prospect = get_object_or_404(Prospect, id=prospect_id, assigned_to=employee)

        # ---- Log Call Result ----
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

        # ---- Add Follow-up ----
        elif action == "add_followup":
            followup_date = request.POST.get("followup_date")
            notes = request.POST.get("notes", "")
            if followup_date:
                CalendarEvent.objects.create(
                    employee=employee,
                    title=f"Follow-up: {prospect.name}",
                    description=notes,
                    event_date=followup_date,
                    related_prospect=prospect,
                )
                messages.success(request, f"Follow-up added for {prospect.name}.")

        return redirect("clients:callingworkspace", list_id=list_id)

    context = {
        "calling_list": calling_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "per_page": per_page,
    }
    return render(request, "calling/callingworkspace.html", context)

@login_required
def employee_calendar(request):
    employee = request.user.employee
    now_ts = timezone.now()
    events = CalendarEvent.objects.filter(employee=employee)

    context = {
        "todays_events": events.filter(scheduled_time__date=now_ts.date()).order_by("scheduled_time"),
        "upcoming_events": events.filter(scheduled_time__date__gt=now_ts.date()).order_by("scheduled_time"),
        "pending_count": events.filter(status="pending").count(),
        "missed_count": events.filter(status="pending", scheduled_time__lt=now_ts).count(),
        "completed_count": events.filter(status="completed").count(),
    }
    return render(request, "calendar/employee_calendar.html", context)

# clients/views.py (add imports at top)
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.http import require_GET
from .models import CalendarEvent, Prospect

# Page view
@login_required
def employee_calendar_page(request):
    """
    Renders the page containing the FullCalendar instance.
    FullCalendar will call the events JSON endpoint to fetch events.
    """
    now_ts = timezone.now()
    events = CalendarEvent.objects.filter(employee=request.user.employee)
    context = {
        "pending_count": events.filter(status="pending").count(),
        "missed_count": events.filter(status="pending", scheduled_time__lt=now_ts).count(),
        "completed_count": events.filter(status="completed").count(),
    }
    return render(request, "calendar/employee_calendar.html", context)


# Events JSON API used by FullCalendar

@require_GET
@login_required
def calendar_events_json(request):
    """
    Returns calendar events as JSON for the currently logged-in employee.
    Compatible with FullCalendar (dayGridMonth, timeGridWeek, timeGridDay).
    """
    employee = request.user.employee

    # Optional: FullCalendar sends `start` and `end` query params
    start = request.GET.get("start")
    end = request.GET.get("end")
    types_param = request.GET.get("types")  # comma-separated types allowed by client-side filter

    events_qs = CalendarEvent.objects.filter(employee=employee)
    if types_param:
        try:
            allowed = [t for t in types_param.split(',') if t]
            if allowed:
                events_qs = events_qs.filter(type__in=allowed)
        except Exception:
            pass

    statuses_param = request.GET.get("statuses")
    sources_param = request.GET.get("sources")

    if start:
        try:
            start_dt = parse_datetime(start)
            if start_dt and timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt)
            events_qs = events_qs.filter(scheduled_time__gte=start_dt)
        except Exception:
            pass

    if end:
        try:
            end_dt = parse_datetime(end)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            events_qs = events_qs.filter(scheduled_time__lte=end_dt)
        except Exception:
            pass

    # Build JSON in FullCalendar expected format
    events = []
    now_ts = timezone.now()
    allowed_statuses = None
    if statuses_param:
        try:
            allowed_statuses = [s for s in statuses_param.split(',') if s]
        except Exception:
            allowed_statuses = None

    allowed_sources = None
    if sources_param:
        try:
            allowed_sources = [s for s in sources_param.split(',') if s]
        except Exception:
            allowed_sources = None

    for e in events_qs:
        source = "manual"
        if e.related_prospect and getattr(e.related_prospect, "calling_list_id", None):
            source = "calling_list"

        status_val = e.status
        if e.status == "pending" and e.scheduled_time and e.scheduled_time < now_ts:
            status_val = "missed"

        # filter by source/status if provided
        if allowed_sources is not None and source not in allowed_sources:
            continue
        if allowed_statuses is not None and status_val not in allowed_statuses:
            continue

        events.append({
            "id": e.id,
            "title": e.title,
            "start": e.scheduled_time.isoformat(),
            "end": e.end_time.isoformat() if e.end_time else None,
            "extendedProps": {
                "type": e.type,
                "notes": e.notes,
                "status": status_val,
                "source": source,
                "related_prospect_id": e.related_prospect.id if e.related_prospect else None,
                "related_prospect_name": e.related_prospect.name if e.related_prospect else None,
            }
        })

    # ---- Include birthdays from Clients and Prospects within the requested range ----
    try:
        # Ensure start_dt/end_dt are available; if not, set wide range
        if 'start_dt' in locals():
            s_dt = start_dt
        else:
            s_dt = timezone.now() - timezone.timedelta(days=365)
        if 'end_dt' in locals():
            e_dt = end_dt
        else:
            e_dt = timezone.now() + timezone.timedelta(days=365)

        # helper to add birthday events
        from datetime import date
        from .models import Client, Prospect

        def add_birthdays(queryset, label_prefix="Birthday"):
            for obj in queryset:
                dob = getattr(obj, 'date_of_birth', None)
                if not dob:
                    continue
                # iterate years between s_dt.year and e_dt.year
                for yr in range(s_dt.year, e_dt.year + 1):
                    try:
                        bday = date(yr, dob.month, dob.day)
                    except ValueError:
                        # skip invalid dates (Feb 29 on non-leap year)
                        continue
                    bday_dt = timezone.make_aware(timezone.datetime.combine(bday, timezone.datetime.min.time()))
                    if bday_dt >= s_dt and bday_dt <= e_dt:
                        events.append({
                            "id": f"birth-{obj.__class__.__name__}-{obj.id}-{yr}",
                            "title": f"{label_prefix}: {getattr(obj, 'name', getattr(obj, 'client', ''))}",
                            "start": bday_dt.isoformat(),
                            "end": None,
                            "extendedProps": {
                                "type": "birthday",
                                "status": "pending",
                                "source": "birthday",
                                "notes": "Auto-generated birthday call",
                                "related_prospect_id": obj.id if obj.__class__.__name__ == 'Prospect' else None,
                            }
                        })

        if not types_param or 'birthday' in (types_param or ''):
            add_birthdays(Client.objects.filter(date_of_birth__isnull=False, mapped_to=employee))
            add_birthdays(Prospect.objects.filter(date_of_birth__isnull=False, assigned_to=employee), label_prefix="Prospect Birthday")
    except Exception:
        pass

    return JsonResponse(events, safe=False)


import json
from django.http import JsonResponse


@login_required
@require_POST
def update_calendar_event(request):
    """Handle drag/resize updates from FullCalendar with CSRF protection."""
    try:
        data = json.loads(request.body)
        event_id = data.get("id")
        start = data.get("start")
        end = data.get("end")

        event = CalendarEvent.objects.get(id=event_id, employee=request.user.employee)

        # parse datetimes safely and make them aware
        if start:
            dt = parse_datetime(start)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            event.scheduled_time = dt or event.scheduled_time
        if end:
            end_dt = parse_datetime(end)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            event.end_time = end_dt or event.end_time

        event.save()
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
@require_POST
def create_calendar_event(request):
    """Create a CalendarEvent for the logged-in employee via AJAX.

    Expects JSON body with: title, scheduled_time (ISO), type, notes, related_prospect_id (optional), client_id (optional)
    Returns the created event in FullCalendar-friendly JSON.
    """
    try:
        data = json.loads(request.body)
        title = data.get("title") or "Untitled"
        scheduled_time = data.get("scheduled_time")
        end_time = data.get("end_time") or data.get("end")
        ev_type = data.get("type") or "task"
        notes = data.get("notes") or ""
        related_prospect_id = data.get("related_prospect_id")
        client_id = data.get("client_id")

        # parse scheduled_time (ISO) to aware datetime
        scheduled_dt = None
        if scheduled_time:
            scheduled_dt = parse_datetime(scheduled_time)
            if scheduled_dt and timezone.is_naive(scheduled_dt):
                scheduled_dt = timezone.make_aware(scheduled_dt)
        else:
            scheduled_dt = timezone.now()

        # lazy imports to avoid circular issues
        from .models import Prospect, Client

        related_prospect = None
        client = None
        if related_prospect_id:
            try:
                related_prospect = Prospect.objects.get(id=related_prospect_id)
            except Prospect.DoesNotExist:
                related_prospect = None
        if client_id:
            try:
                client = Client.objects.get(id=client_id)
            except Exception:
                client = None

        end_dt = None
        if end_time:
            end_dt = parse_datetime(end_time)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)

        event = CalendarEvent.objects.create(
            employee=request.user.employee,
            title=title,
            type=ev_type,
            notes=notes,
            scheduled_time=scheduled_dt,
            end_time=end_dt,
            related_prospect=related_prospect,
            client=client,
        )

        ev_json = {
            "id": event.id,
            "title": event.title,
            "start": event.scheduled_time.isoformat(),
            "end": event.end_time.isoformat() if event.end_time else None,
            "extendedProps": {
                "type": event.type,
                "notes": event.notes,
                "status": event.status,
                "related_prospect_id": event.related_prospect.id if event.related_prospect else None,
                "related_prospect_name": event.related_prospect.name if event.related_prospect else None,
            }
        }

        return JsonResponse({"success": True, "event": ev_json})
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)


@login_required
@require_POST
def delete_calendar_event(request):
    """Delete a CalendarEvent owned by the requesting employee.

    Expects JSON body: { id: <event id> }
    """
    try:
        data = json.loads(request.body)
        event_id = data.get('id')
        if not event_id:
            return JsonResponse({'success': False, 'error': 'Missing id'}, status=400)

        # allow deletion of auto-generated birthday events (synthetic ids) only by ignoring them
        # ensure event belongs to user
        if str(event_id).startswith('birth-'):
            # synthetic birthday events are not deletable
            return JsonResponse({'success': False, 'error': 'Birthday events cannot be deleted'}, status=400)

        event = CalendarEvent.objects.filter(id=event_id, employee=request.user.employee).first()
        if not event:
            return JsonResponse({'success': False, 'error': 'Event not found or permission denied'}, status=404)
        event.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def update_calendar_event_details(request):
    """Update title/type/notes/scheduled_time for an existing CalendarEvent owned by the user.

    Expects JSON body: { id, title?, scheduled_time?, type?, notes? }
    """
    try:
        data = json.loads(request.body)
        event_id = data.get('id')
        if not event_id:
            return JsonResponse({'success': False, 'error': 'Missing id'}, status=400)

        event = CalendarEvent.objects.filter(id=event_id, employee=request.user.employee).first()
        if not event:
            return JsonResponse({'success': False, 'error': 'Event not found or permission denied'}, status=404)

        title = data.get('title')
        scheduled_time = data.get('scheduled_time')
        ev_type = data.get('type')
        notes = data.get('notes')
        end_time = data.get('end_time') or data.get('end')

        if title is not None:
            event.title = title
        if ev_type is not None:
            event.type = ev_type
        if notes is not None:
            event.notes = notes
        if scheduled_time is not None:
            dt = parse_datetime(scheduled_time)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            event.scheduled_time = dt

        if end_time is not None:
            end_dt = parse_datetime(end_time)
            if end_dt and timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt)
            event.end_time = end_dt

        event.save()

        return JsonResponse({'success': True, 'event': {
            'id': event.id,
            'title': event.title,
            'start': event.scheduled_time.isoformat(),
            'end': event.end_time.isoformat() if event.end_time else None,
            'extendedProps': {
                'type': event.type,
                'notes': event.notes,
                'status': event.status,
            }
        }})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

@login_required
def delete_calling_list(request, list_id):
    calling_list = get_object_or_404(CallingList, id=list_id)

    # Only admin should delete
    if request.user.employee.role != "admin":
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

        # ✅ Update prospect
        prospect.status = status
        prospect.last_contacted = timezone.now()
        if notes:
            prospect.notes = (prospect.notes or "") + f"\n{timezone.now().strftime('%Y-%m-%d %H:%M')}: {notes}"
        prospect.save()

        # ✅ Auto-create follow-up if status is "follow_up"
        if status == "follow_up":
            CalendarEvent.objects.create(
                employee=request.user.employee,
                title=f"Follow-up: {prospect.name}",
                scheduled_time=timezone.now() + timezone.timedelta(days=1),  # default +1 day
                type="follow_up",
                notes=notes,
                related_prospect=prospect,
            )

        messages.success(request, f"Call result logged for {prospect.name}")
        return redirect("clients:callingworkspace", list_id=prospect.calling_list.id)

    return render(request, "calling/log_result.html", {"prospect": prospect})


from django.utils import timezone

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


from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import CalendarEvent


@login_required
def mark_done(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)
    event.status = "completed"
    event.save()
    messages.success(request, "Event marked as completed ✅")
    return redirect("clients:employee_dashboard")

@login_required
def skip_event(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)
    event.status = "skipped"
    event.save()
    messages.warning(request, "Event skipped ❌")
    return redirect("clients:employee_dashboard")

@login_required
def reschedule_event(request, event_id):
    event = get_object_or_404(CalendarEvent, id=event_id, employee=request.user.employee)

    if request.method == "POST":
        new_time = request.POST.get("scheduled_time")
        if new_time:
            event.scheduled_time = new_time
            event.status = "rescheduled"
            event.save()
            messages.success(request, "Event rescheduled 🔄")
            return redirect("clients:employee_dashboard")

    return render(request, "calendar/reschedule_event.html", {"event": event})
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from .models import Client, MessageTemplate
from .utils.phone_utils import normalize_phone
from django.views.decorators.http import require_GET
from urllib.parse import quote as urlquote
import csv
from django.http import HttpResponse

@login_required
@require_POST
def bulk_whatsapp(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        template_id = data.get("template_id")
        client_ids = data.get("client_ids", [])
        preview_only = data.get("preview", False)

        # Validate input
        if not template_id or not client_ids:
            return JsonResponse({"error": "Missing template_id or client_ids"}, status=400)

        template = MessageTemplate.objects.filter(id=template_id).first()
        if not template:
            return JsonResponse({"error": "Template not found"}, status=404)

        clients = Client.objects.filter(id__in=client_ids)
        if not clients.exists():
            return JsonResponse({"error": "No valid clients found"}, status=404)

        messages_preview = []
        sent_count = 0
        skipped = []

        # Render messages using MessageTemplate.render which supports Django template syntax
        for client in clients:
            # normalize/validate phone
            e164, wa_number = normalize_phone(client.phone)
            if not e164:
                skipped.append({"id": client.id, "name": client.name, "phone": client.phone})
                continue

            try:
                # Provide sender_name and any other useful context to templates
                sender_name = None
                try:
                    # prefer full name from User or Employee if available
                    if hasattr(request.user, 'get_full_name') and request.user.get_full_name():
                        sender_name = request.user.get_full_name()
                    elif hasattr(request.user, 'employee') and getattr(request.user.employee, 'name', None):
                        sender_name = request.user.employee.name
                    else:
                        sender_name = getattr(request.user, 'username', '')
                except Exception:
                    sender_name = getattr(request.user, 'username', '')

                rendered = template.render(client, extra_context={"sender_name": sender_name})
            except Exception:
                # fallback to raw content if render fails
                rendered = template.content

            messages_preview.append({
                "id": client.id,
                "client": client.name,
                "phone": e164,
                "wa_number": wa_number,
                "message": rendered
            })

        # If preview only, don't create logs / queue messages
        if preview_only:
            return JsonResponse({"messages_preview": messages_preview, "sent_count": 0, "skipped": skipped})

        # Create MessageLog entries (queued). Actual delivery is handled by management command / background worker.
        from .models import MessageLog

        queued_count = 0
        for msg in messages_preview:
            # find original client object by id
            try:
                cli = Client.objects.get(id=msg.get('id'))
            except Exception:
                cli = None
            ml = MessageLog.objects.create(
                template=template,
                client=cli,
                recipient_phone=msg["phone"],
                message_text=msg["message"],
                status="queued",
                created_by=request.user,
            )
            queued_count += 1

        return JsonResponse({"messages_preview": messages_preview, "sent_count": queued_count, "skipped": skipped})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_GET
def wa_preview_page(request):
    """Render a simple page with wa.me links and QR codes for selected clients.

    Expects GET params: template_id, client_ids (comma separated)
    """
    template_id = request.GET.get('template_id')
    client_ids = request.GET.get('client_ids', '')
    if not template_id or not client_ids:
        return HttpResponse('Missing parameters', status=400)
    try:
        tpl = MessageTemplate.objects.get(id=int(template_id))
    except Exception:
        return HttpResponse('Template not found', status=404)
    ids = [int(x) for x in client_ids.split(',') if x.strip().isdigit()]
    clients = Client.objects.filter(id__in=ids)
    previews = []
    for c in clients:
        e164, wa = normalize_phone(c.phone)
        if not e164:
            continue
        # provide sender_name in rendering
        sender_name = None
        try:
            if hasattr(request.user, 'get_full_name') and request.user.get_full_name():
                sender_name = request.user.get_full_name()
            elif hasattr(request.user, 'employee') and getattr(request.user.employee, 'name', None):
                sender_name = request.user.employee.name
            else:
                sender_name = getattr(request.user, 'username', '')
        except Exception:
            sender_name = getattr(request.user, 'username', '')
        msg = tpl.render(c, extra_context={"sender_name": sender_name})
        # truncate to 1000 chars
        if len(msg) > 1000:
            msg = msg[:1000]
        link = f"https://wa.me/{wa}?text={urlquote(msg)}"
        qr_src = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urlquote(link)}"
        previews.append({'client': c.name, 'phone': e164, 'wa': wa, 'message': msg, 'link': link, 'qr': qr_src})

    return render(request, 'clients/wa_preview_page.html', {'previews': previews, 'template': tpl})


@login_required
@require_GET
def wa_preview_csv(request):
    template_id = request.GET.get('template_id')
    client_ids = request.GET.get('client_ids', '')
    if not template_id or not client_ids:
        return HttpResponse('Missing parameters', status=400)
    try:
        tpl = MessageTemplate.objects.get(id=int(template_id))
    except Exception:
        return HttpResponse('Template not found', status=404)
    ids = [int(x) for x in client_ids.split(',') if x.strip().isdigit()]
    clients = Client.objects.filter(id__in=ids)

    # Build CSV
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="wa_previews.csv"'
    writer = csv.writer(resp)
    writer.writerow(['client_id', 'client_name', 'phone_e164', 'wa_number', 'message', 'wa_link'])
    for c in clients:
        e164, wa = normalize_phone(c.phone)
        if not e164:
            continue
        sender_name = None
        try:
            if hasattr(request.user, 'get_full_name') and request.user.get_full_name():
                sender_name = request.user.get_full_name()
            elif hasattr(request.user, 'employee') and getattr(request.user.employee, 'name', None):
                sender_name = request.user.employee.name
            else:
                sender_name = getattr(request.user, 'username', '')
        except Exception:
            sender_name = getattr(request.user, 'username', '')
        msg = tpl.render(c, extra_context={"sender_name": sender_name})
        if len(msg) > 1000:
            msg = msg[:1000]
        link = f"https://wa.me/{wa}?text={urlquote(msg)}"
        writer.writerow([c.id, c.name, e164, wa, msg, link])
    return resp


@login_required
def edit_client(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    # ✅ Allow both admin and employees — but employee can only edit their own mapped clients
    if hasattr(request.user, "employee") and request.user.employee.role == "employee":
        if client.mapped_to != request.user.employee:
            messages.error(request, "You can edit only your assigned clients.")
            return redirect("clients:my_clients")

    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(request, "Client updated successfully!")
            if request.user.employee.role == "admin":
                return redirect("clients:all_clients")
            else:
                return redirect("clients:my_clients")
    else:
        form = ClientForm(instance=client)

    return render(request, "clients/edit_client.html", {"form": form, "client": client})


# app/views.py
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.contrib import messages

from .models import Client, Employee
from .forms import ClientReassignForm

@login_required
@permission_required('clients.change_client', raise_exception=True)
def client_reassign_view(request, client_id):
    client = get_object_or_404(Client, id=client_id)

    if request.method == 'POST':
        form = ClientReassignForm(request.POST)
        if form.is_valid():
            new_employee = form.cleaned_data['new_employee']
            note = form.cleaned_data.get('note', '')
            changed, previous, new = client.reassign_to(new_employee, changed_by=request.user, note=note)
            if changed:
                messages.success(request, f"Reassigned client to {new_employee.user.username if new_employee else 'Unassigned'}.")
            else:
                messages.info(request, "No change — client already assigned to that employee.")
            return redirect(reverse('clients:detail', args=[client.id]))
    else:
        form = ClientReassignForm(initial={'new_employee': client.mapped_to})

    return render(request, 'clients/reassign_modal.html', {'client': client, 'form': form})


# app/views.py
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseForbidden
from django.db import transaction

@login_required
def bulk_reassign_view(request):
    """
    Three-step behaviour in one view:
    - GET: show the form to choose source + target
    - POST action='load' : show clients belonging to selected source_employee (preview & select)
    - POST action='apply': perform reassign for selected client ids to target_employee
    """
    employees = Employee.objects.all().select_related('user')
    context = {'employees': employees, 'clients_preview': None, 'source_emp': None, 'target_emp': None}

    if request.method == 'POST':
        action = request.POST.get('action')

        # LOAD clients for preview
        if action == 'load':
            try:
                source_id = int(request.POST.get('source_employee') or 0)
            except (TypeError, ValueError):
                messages.error(request, "Please choose a source employee.")
                return render(request, 'clients/bulk_reassign.html', context)

            source_emp = get_object_or_404(Employee, pk=source_id)
            clients_qs = Client.objects.filter(mapped_to=source_emp).order_by('id')
            context.update({
                'clients_preview': clients_qs,
                'source_emp': source_emp,
                'target_emp': None,
            })
            return render(request, 'clients/bulk_reassign.html', context)

        # APPLY reassignment for selected client ids
        if action == 'apply':
            try:
                source_id = int(request.POST.get('source_employee') or 0)
            except (TypeError, ValueError):
                messages.error(request, "Source employee missing.")
                return redirect('clients:bulk_reassign')

            try:
                target_id = int(request.POST.get('target_employee') or 0)
            except (TypeError, ValueError):
                messages.error(request, "Target employee missing.")
                return redirect('clients:bulk_reassign')

            source_emp = get_object_or_404(Employee, pk=source_id)
            target_emp = get_object_or_404(Employee, pk=target_id)

            # collect selected client ids from checkboxes named selected_client
            selected = request.POST.getlist('selected_client')
            if not selected:
                messages.error(request, "No clients selected for reassignment.")
                return redirect('clients:bulk_reassign')

            # validate they actually belong to source_emp (safety)
            clients_to_move = Client.objects.filter(id__in=selected, mapped_to=source_emp)

            if not clients_to_move.exists():
                messages.error(request, "No valid clients found to reassign (maybe selection mismatch).")
                return redirect('clients:bulk_reassign')

            moved_count = 0
            # Do reassign within transaction
            with transaction.atomic():
                for c in clients_to_move:
                    changed, prev, new = c.reassign_to(target_emp, changed_by=request.user, note="Bulk reassign via admin page")
                    if changed:
                        moved_count += 1

            messages.success(request, f"Reassigned {moved_count} client(s) from {source_emp.user.username} to {target_emp.user.username}.")
            return redirect('clients:bulk_reassign')

    return render(request, 'clients/bulk_reassign.html', context)

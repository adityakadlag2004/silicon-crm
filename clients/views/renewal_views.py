import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import EditRenewalForm, RenewalForm
from ..models import Client, Renewal, Product
from .helpers import get_manager_access


def _no_name_product_ids():
	return list(
		Product.objects.filter(
			Q(code__in=["LIFE_INS", "HEALTH_INS"]) | Q(name__in=["Life Insurance", "Health Insurance"])
		).values_list("id", flat=True)
	)


@login_required
@require_POST
def quick_add_client_for_renewal(request):
	"""Create a client from renewal modal when search returns no result."""
	try:
		payload = json.loads(request.body.decode("utf-8"))
	except Exception:
		return JsonResponse({"ok": False, "error": "Invalid payload."}, status=400)

	name = (payload.get("name") or "").strip().upper()
	phone = (payload.get("phone") or "").strip().upper()
	email = (payload.get("email") or "").strip().upper()
	address = (payload.get("address") or "").strip().upper()
	pan = (payload.get("pan") or "").strip().upper()

	if not name or not phone or not email:
		return JsonResponse({"ok": False, "error": "Name, phone and email are required."}, status=400)

	user_emp = getattr(request.user, "employee", None)
	client = Client(
		name=name,
		phone=phone or None,
		email=email or None,
		address=address or None,
		pan=pan or None,
	)

	if user_emp and user_emp.role == "employee":
		client.mapped_to = user_emp
		client.status = "Mapped"
	client.save()

	return JsonResponse(
		{
			"ok": True,
			"client": {
				"id": client.id,
				"text": f"{client.name} ({client.email or ''} {client.phone or ''})",
			},
		}
	)


@login_required
def add_renewal(request, client_id=None):
	client = get_object_or_404(Client, id=client_id) if client_id else None
	user_emp = getattr(request.user, "employee", None)
	is_admin_user = request.user.is_superuser or (user_emp and user_emp.role == "admin")

	if request.method == "POST":
		form = RenewalForm(request.POST)
		if form.is_valid():
			renewal = form.save(commit=False)

			if client is not None:
				renewal.client = client
			elif not renewal.client_id:
				raw_client_id = request.POST.get("client")
				if not raw_client_id:
					form.add_error("client", "Please select a client.")
					return render(request, "renewals/add_renewal.html", {"form": form, "client": client})
				renewal.client = get_object_or_404(Client, id=raw_client_id)

			if user_emp and user_emp.role == "employee":
				renewal.employee = user_emp
			elif not renewal.employee_id and user_emp:
				renewal.employee = user_emp

			renewal.created_by = request.user
			renewal.save()
			messages.success(request, "Renewal business entry added successfully!")
			return redirect("clients:all_renewals")
	else:
		initial = {
			"renewal_date": date.today(),
			"premium_collected_on": date.today(),
		}
		if client is not None:
			initial["client"] = client.id
		if user_emp:
			initial["employee"] = user_emp.id
		form = RenewalForm(initial=initial)

	if user_emp and user_emp.role == "employee" and "employee" in form.fields:
		form.fields["employee"].disabled = True

	context = {
		"form": form,
		"client": client,
		"client_label": f"{client.name} ({client.email or ''} {client.phone or ''})" if client else "",
		"is_admin_user": bool(is_admin_user),
		"no_name_product_ids": _no_name_product_ids(),
	}
	return render(request, "renewals/add_renewal.html", context)


@login_required
def all_renewals(request):
	renewals_qs = Renewal.objects.select_related("client", "employee__user", "created_by").all().order_by(
		"-premium_collected_on", "-created_at"
	)

	user_emp = getattr(request.user, "employee", None)
	is_manager = bool(user_emp and user_emp.role == "manager")
	manager_access = get_manager_access() if is_manager else None

	if user_emp and user_emp.role == "employee":
		renewals_qs = renewals_qs.filter(employee=user_emp)
	elif is_manager and manager_access and not manager_access.allow_view_all_sales:
		renewals_qs = renewals_qs.filter(employee=user_emp)

	scoped_qs = renewals_qs

	q = (request.GET.get("q") or "").strip()
	product_ref = (request.GET.get("product") or "").strip()
	frequency = (request.GET.get("frequency") or "").strip()
	employee = (request.GET.get("employee") or "").strip()
	start_date = (request.GET.get("start_date") or "").strip()
	end_date = (request.GET.get("end_date") or "").strip()
	payment_start = (request.GET.get("payment_start") or "").strip()
	payment_end = (request.GET.get("payment_end") or "").strip()

	if q:
		renewals_qs = renewals_qs.filter(
			Q(client__name__icontains=q)
			| Q(client__email__icontains=q)
			| Q(client__phone__icontains=q)
			| Q(product_name__icontains=q)
			| Q(notes__icontains=q)
		)
	if product_ref:
		renewals_qs = renewals_qs.filter(product_ref_id=product_ref)
	if frequency in dict(Renewal.FREQUENCY_CHOICES):
		renewals_qs = renewals_qs.filter(frequency=frequency)
	if employee:
		renewals_qs = renewals_qs.filter(
			Q(employee__user__username__icontains=employee)
			| Q(employee__user__first_name__icontains=employee)
			| Q(employee__user__last_name__icontains=employee)
		)
	if start_date and end_date:
		renewals_qs = renewals_qs.filter(renewal_date__range=[start_date, end_date])
	if payment_start and payment_end:
		renewals_qs = renewals_qs.filter(premium_collected_on__range=[payment_start, payment_end])

	if not (q or product_ref or frequency or employee or start_date or end_date or payment_start or payment_end):
		renewals_qs = renewals_qs.filter(premium_collected_on=date.today())

	today = date.today()
	today_qs = scoped_qs.filter(premium_collected_on=today)
	today_submission_total = today_qs.aggregate(total=Sum("premium_amount"))["total"] or 0
	today_submission_count = today_qs.count()

	# Month-to-date premium collection (collected_on between 1st of this month and today).
	month_start = today.replace(day=1)
	month_qs = scoped_qs.filter(premium_collected_on__range=[month_start, today])
	month_submission_total = month_qs.aggregate(total=Sum("premium_amount"))["total"] or 0
	month_submission_count = month_qs.count()
	month_label = today.strftime("%B %Y")

	filtered_total_premium = renewals_qs.aggregate(total=Sum("premium_amount"))["total"] or 0

	paginator = Paginator(renewals_qs, 50)
	page_number = request.GET.get("page")
	page_obj = paginator.get_page(page_number)

	qdict = request.GET.copy()
	qdict.pop("page", None)
	qstring = qdict.urlencode()

	context = {
		"renewals": page_obj,
		"is_employee": bool(user_emp and user_emp.role == "employee"),
		"is_manager": is_manager,
		"manager_can_edit": bool(is_manager and manager_access and manager_access.allow_edit_sales),
		"qstring": qstring,
		"today_submission_total": today_submission_total,
		"today_submission_count": today_submission_count,
		"month_submission_total": month_submission_total,
		"month_submission_count": month_submission_count,
		"month_label": month_label,
		"filtered_total_premium": filtered_total_premium,
		"product_options": Product.objects.filter(domain__in=[Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH]).order_by("display_order", "name"),
	}
	return render(request, "renewals/all_renewals.html", context)


@login_required
def edit_renewal(request, renewal_id):
	renewal = get_object_or_404(Renewal, id=renewal_id)
	user_emp = getattr(request.user, "employee", None)
	is_admin_user = request.user.is_superuser or (user_emp and user_emp.role == "admin")
	is_manager = bool(user_emp and user_emp.role == "manager")
	mgr_access = get_manager_access() if is_manager else None

	if (
		not is_admin_user
		and not (is_manager and mgr_access and mgr_access.allow_edit_sales)
		and (not user_emp or renewal.employee_id != user_emp.id)
	):
		return HttpResponseForbidden("You do not have permission to edit this renewal.")

	if request.method == "POST":
		form = EditRenewalForm(request.POST, instance=renewal)
		if form.is_valid():
			updated = form.save(commit=False)
			if user_emp and user_emp.role == "employee":
				updated.employee = user_emp
			updated.save()
			messages.success(request, "Renewal updated successfully!")
			return redirect("clients:all_renewals")
	else:
		form = EditRenewalForm(instance=renewal)

	if user_emp and user_emp.role == "employee" and "employee" in form.fields:
		form.fields["employee"].disabled = True

	return render(
		request,
		"renewals/edit_renewal.html",
		{"form": form, "renewal": renewal, "no_name_product_ids": _no_name_product_ids()},
	)


@login_required
def delete_renewal(request, renewal_id):
	renewal = get_object_or_404(Renewal, id=renewal_id)
	user_emp = getattr(request.user, "employee", None)
	is_admin_user = request.user.is_superuser or (user_emp and user_emp.role == "admin")

	if not is_admin_user and (not user_emp or renewal.employee_id != user_emp.id):
		return HttpResponseForbidden("You do not have permission to delete this renewal.")

	if request.method == "POST":
		renewal.delete()
		messages.success(request, "Renewal deleted successfully!")
		return redirect("clients:all_renewals")

	return render(request, "renewals/delete_renewal.html", {"renewal": renewal})

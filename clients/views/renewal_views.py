import calendar
import json
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import permissions
from ..forms import EditRenewalForm, RenewalForm
from ..templatetags.custom_filters import inr
from ..models import Client, Renewal, Product
from .helpers import parse_date_param, name_words_q
from .helpers import get_manager_access, success_with_drive_link as _success_with_drive_link


def _renewal_is_insurance(renewal):
	return renewal.insurance_kind in ("health", "life")


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
	is_admin_user = permissions.is_admin(request.user)

	if request.method == "POST":
		form = RenewalForm(request.POST)
		if form.is_valid():
			renewal = form.save(commit=False)

			# product_type is derived in the form's clean() but isn't a model-
			# form field, so it must be copied onto the instance explicitly —
			# the Health/Life breakdown and tracker sync both key off it.
			renewal.product_type = form.cleaned_data.get("product_type") or renewal.product_type

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

			# Link this renewal to a policy on the Insurance Tracker: the
			# existing one the user ticked, or a new one created from the
			# number they typed (old book sold before the tracker existed).
			from ..services import insurance_sync
			try:
				insurance_sync.link_renewal_to_policy(
					renewal,
					selected_policy_id=(request.POST.get("policy") or "").strip() or None,
					new_policy_number=request.POST.get("policy_number") or "",
				)
			except Exception:
				pass

			# Offer to file the policy document in the client's Drive folder —
			# a link, not a forced redirect, so daily bulk entry isn't
			# interrupted. The Drive view creates the folder if it's missing.
			_success_with_drive_link(
				request, "Renewal business entry added.", renewal.client,
				insurance=_renewal_is_insurance(renewal))
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
	elif is_manager and not permissions.can(request.user, "view_all_sales"):
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

	# Default to the current month's renewal business when no filters are applied.
	today = date.today()
	any_filter = bool(
		q or product_ref or frequency or employee or start_date or end_date or payment_start or payment_end
	)
	if not any_filter:
		payment_start = today.replace(day=1).isoformat()
		payment_end = today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()

	if q:
		renewals_qs = renewals_qs.filter(
			name_words_q("client__name", q)
			| Q(client__email__icontains=q)
			| Q(client__phone__icontains=q)
			| Q(product_name__icontains=q)
			| Q(notes__icontains=q)
		)
	if product_ref:
		# Dropdown offers main products only; match the chosen main product and
		# any renewal booked under one of its sub-products.
		renewals_qs = renewals_qs.filter(
			Q(product_ref_id=product_ref) | Q(product_ref__parent_id=product_ref)
		)
	if frequency in dict(Renewal.FREQUENCY_CHOICES):
		renewals_qs = renewals_qs.filter(frequency=frequency)
	if employee:
		renewals_qs = renewals_qs.filter(
			Q(employee__user__username__icontains=employee)
			| Q(employee__user__first_name__icontains=employee)
			| Q(employee__user__last_name__icontains=employee)
		)
	renewal_from, renewal_to = parse_date_param(start_date), parse_date_param(end_date)
	if renewal_from and renewal_to:
		renewals_qs = renewals_qs.filter(renewal_date__range=[renewal_from, renewal_to])
	payment_from, payment_to = parse_date_param(payment_start), parse_date_param(payment_end)
	if payment_from and payment_to:
		renewals_qs = renewals_qs.filter(premium_collected_on__range=[payment_from, payment_to])

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

	today = timezone.localdate()

	# This-month renewal business, split by Health / Life / Other \u2014 the figure
	# the team actually reports on. Scoped like the list (an employee sees only
	# their own), measured on when the premium was collected.
	month_start = today.replace(day=1)
	month_biz = scoped_qs.filter(premium_collected_on__range=[month_start, today])

	def _type_biz(kind):
		rows = month_biz.filter(Renewal.kind_q(kind)).aggregate(
			amount=Sum("premium_amount"), count=Count("id"))
		return {"amount": rows["amount"] or 0, "count": rows["count"] or 0}

	health_biz = _type_biz("health")
	life_biz = _type_biz("life")
	other_biz = _type_biz("other")

	agg = Renewal.objects.aggregate(
		total=Count("id"),
		due_30=Count("id", filter=Q(renewal_end_date__gte=today,
		                            renewal_end_date__lte=today + timedelta(days=30))),
		overdue=Count("id", filter=Q(renewal_end_date__lt=today)),
		premium=Sum("premium_amount"),
	)
	context = {
		# KPI strip leads with this month's business split by product line,
		# since that is what the renewals desk tracks day to day.
		"kpis": [
			{"label": f"Health \u00b7 {month_label}", "color": "#15803D",
			 "value": f"\u20b9{inr(health_biz['amount'])}",
			 "sub": f"{health_biz['count']} renewal{'' if health_biz['count'] == 1 else 's'}"},
			{"label": f"Life \u00b7 {month_label}", "color": "#4338CA",
			 "value": f"\u20b9{inr(life_biz['amount'])}",
			 "sub": f"{life_biz['count']} renewal{'' if life_biz['count'] == 1 else 's'}"},
			{"label": f"This Month", "color": "#B45309",
			 "value": f"\u20b9{inr(month_submission_total)}",
			 "sub": f"{month_submission_count} total"},
			{"label": "Due \u226430 days", "value": agg["due_30"], "color": "#0369A1"},
			{"label": "Overdue", "value": agg["overdue"], "color": "#BE123C"},
		],
		"health_biz": health_biz,
		"life_biz": life_biz,
		"other_biz": other_biz,
		"renewals": page_obj,
		"is_employee": bool(user_emp and user_emp.role == "employee"),
		"is_manager": is_manager,
		"manager_can_edit": bool(is_manager and permissions.can(request.user, "edit_sales")),
		"qstring": qstring,
		"today_submission_total": today_submission_total,
		"today_submission_count": today_submission_count,
		"month_submission_total": month_submission_total,
		"month_submission_count": month_submission_count,
		"month_label": month_label,
		"filtered_total_premium": filtered_total_premium,
		"filter_payment_start": payment_start,
		"filter_payment_end": payment_end,
		"product_options": Product.objects.filter(parent__isnull=True, domain__in=[Product.DOMAIN_RENEWAL, Product.DOMAIN_BOTH]).order_by("display_order", "name"),
	}
	return render(request, "renewals/all_renewals.html", context)


@login_required
def edit_renewal(request, renewal_id):
	renewal = get_object_or_404(Renewal, id=renewal_id)
	user_emp = getattr(request.user, "employee", None)
	is_admin_user = permissions.is_admin(request.user)
	is_manager = bool(user_emp and user_emp.role == "manager")
	mgr_access = get_manager_access() if is_manager else None

	if (
		not is_admin_user
		and not permissions.can(request.user, "edit_sales")
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
	is_admin_user = permissions.is_admin(request.user)

	if not is_admin_user and (not user_emp or renewal.employee_id != user_emp.id):
		return HttpResponseForbidden("You do not have permission to delete this renewal.")

	if request.method == "POST":
		renewal.delete()
		messages.success(request, "Renewal deleted successfully!")
		return redirect("clients:all_renewals")

	return render(request, "renewals/delete_renewal.html", {"renewal": renewal})

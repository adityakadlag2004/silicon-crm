"""Target & Special Campaign views: management page + AJAX CRUD.

Mirrors the incentive-rules builder in ``sales.py``. Campaigns are time-bound,
product-wise promotions; during a campaign's window, sales of its products earn
the campaign benefit instead of the regular IncentiveRule (handled in
``Sale.compute_points``).
"""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import (
    Campaign,
    CampaignProduct,
    CampaignSlab,
    Product,
    campaign_product_overlaps,
)


def _is_admin(request):
    user_emp = getattr(request.user, "employee", None)
    return request.user.is_superuser or (user_emp and user_emp.role == "admin")


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@login_required
def manage_campaigns(request):
    """Campaigns builder page (admin only)."""
    if not _is_admin(request):
        messages.error(request, "You do not have permission to access campaigns.")
        return redirect("clients:admin_dashboard")

    campaigns = (
        Campaign.objects.prefetch_related("products__slabs", "products__product_ref").all()
    )
    product_options = Product.objects.filter(
        is_active=True,
        archived_at__isnull=True,
        domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
    ).order_by("display_order", "name")

    return render(
        request,
        "campaigns/manage_campaigns.html",
        {"campaigns": campaigns, "product_options": product_options},
    )


# ----------------------------- Campaign CRUD -----------------------------
@login_required
@require_POST
def add_campaign(request):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    try:
        data = json.loads(request.body)
        name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        start_date = _parse_date(data.get("start_date"))
        end_date = _parse_date(data.get("end_date"))

        if not name:
            return JsonResponse({"error": "Campaign name is required."}, status=400)
        if not start_date or not end_date:
            return JsonResponse({"error": "Valid start and end dates are required."}, status=400)
        if end_date < start_date:
            return JsonResponse({"error": "End date cannot be before start date."}, status=400)

        campaign = Campaign.objects.create(
            name=name,
            description=description,
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            created_by=request.user,
        )
        return JsonResponse({
            "success": True,
            "campaign": {
                "id": campaign.id,
                "name": campaign.name,
                "description": campaign.description,
                "start_date": campaign.start_date.isoformat(),
                "end_date": campaign.end_date.isoformat(),
                "is_active": campaign.is_active,
            },
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def update_campaign(request, campaign_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    campaign = get_object_or_404(Campaign, id=campaign_id)
    try:
        data = json.loads(request.body)
        new_start = campaign.start_date
        new_end = campaign.end_date
        if "name" in data:
            name = (data["name"] or "").strip()
            if not name:
                return JsonResponse({"error": "Campaign name is required."}, status=400)
            campaign.name = name
        if "description" in data:
            campaign.description = (data["description"] or "").strip()
        if "start_date" in data:
            new_start = _parse_date(data["start_date"])
            if not new_start:
                return JsonResponse({"error": "Invalid start date."}, status=400)
        if "end_date" in data:
            new_end = _parse_date(data["end_date"])
            if not new_end:
                return JsonResponse({"error": "Invalid end date."}, status=400)
        if new_end < new_start:
            return JsonResponse({"error": "End date cannot be before start date."}, status=400)

        # If dates change, ensure no product on this campaign now overlaps another campaign.
        if (new_start, new_end) != (campaign.start_date, campaign.end_date):
            for cp in campaign.products.all():
                if campaign_product_overlaps(cp.product_ref, new_start, new_end, exclude_campaign_id=campaign.id):
                    return JsonResponse(
                        {"error": f"New dates overlap an existing campaign for '{cp.product_ref.name}'."},
                        status=400,
                    )
        campaign.start_date = new_start
        campaign.end_date = new_end

        if "is_active" in data:
            campaign.is_active = bool(data["is_active"])
        campaign.save()
        return JsonResponse({"success": True, "message": f"'{campaign.name}' updated."})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def delete_campaign(request, campaign_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    campaign = get_object_or_404(Campaign, id=campaign_id)
    name = campaign.name
    campaign.delete()
    return JsonResponse({"success": True, "message": f"Campaign '{name}' deleted."})


# ------------------------- CampaignProduct CRUD -------------------------
@login_required
@require_POST
def add_campaign_product(request, campaign_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    campaign = get_object_or_404(Campaign, id=campaign_id)
    try:
        data = json.loads(request.body)
        product = Product.objects.filter(
            pk=data.get("product_id"),
            is_active=True,
            archived_at__isnull=True,
            domain__in=[Product.DOMAIN_SALE, Product.DOMAIN_BOTH],
        ).first()
        if not product:
            return JsonResponse({"error": "Invalid product selection."}, status=400)

        if CampaignProduct.objects.filter(campaign=campaign, product_ref=product).exists():
            return JsonResponse({"error": f"'{product.name}' is already in this campaign."}, status=400)

        if campaign_product_overlaps(product, campaign.start_date, campaign.end_date, exclude_campaign_id=campaign.id):
            return JsonResponse(
                {"error": f"'{product.name}' is already in another campaign overlapping these dates."},
                status=400,
            )

        benefit_type = data.get("benefit_type") or CampaignProduct.BENEFIT_UNIT
        if benefit_type not in (CampaignProduct.BENEFIT_UNIT, CampaignProduct.BENEFIT_TARGET):
            return JsonResponse({"error": "Invalid benefit type."}, status=400)

        unit_amount = _dec(data.get("unit_amount")) if benefit_type == CampaignProduct.BENEFIT_UNIT else None
        points_per_unit = _dec(data.get("points_per_unit")) if benefit_type == CampaignProduct.BENEFIT_UNIT else None
        if benefit_type == CampaignProduct.BENEFIT_UNIT and (not unit_amount or unit_amount <= 0):
            return JsonResponse({"error": "Unit amount must be positive."}, status=400)

        cp = CampaignProduct.objects.create(
            campaign=campaign,
            product_ref=product,
            benefit_type=benefit_type,
            unit_amount=unit_amount,
            points_per_unit=points_per_unit,
        )
        return JsonResponse({
            "success": True,
            "product": {
                "id": cp.id,
                "product_id": product.id,
                "product_name": product.name,
                "benefit_type": cp.benefit_type,
                "unit_amount": str(cp.unit_amount) if cp.unit_amount is not None else "",
                "points_per_unit": str(cp.points_per_unit) if cp.points_per_unit is not None else "",
            },
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def update_campaign_product(request, product_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    cp = get_object_or_404(CampaignProduct, id=product_id)
    try:
        data = json.loads(request.body)
        if "benefit_type" in data:
            benefit_type = data["benefit_type"]
            if benefit_type not in (CampaignProduct.BENEFIT_UNIT, CampaignProduct.BENEFIT_TARGET):
                return JsonResponse({"error": "Invalid benefit type."}, status=400)
            cp.benefit_type = benefit_type
        if "unit_amount" in data:
            cp.unit_amount = _dec(data["unit_amount"])
        if "points_per_unit" in data:
            cp.points_per_unit = _dec(data["points_per_unit"])
        if cp.benefit_type == CampaignProduct.BENEFIT_UNIT and (not cp.unit_amount or cp.unit_amount <= 0):
            return JsonResponse({"error": "Unit amount must be positive."}, status=400)
        cp.save()
        return JsonResponse({"success": True, "message": f"{cp.product_ref.name} updated."})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def delete_campaign_product(request, product_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    cp = get_object_or_404(CampaignProduct, id=product_id)
    name = cp.product_ref.name
    cp.delete()
    return JsonResponse({"success": True, "message": f"'{name}' removed from campaign."})


# --------------------------- CampaignSlab CRUD ---------------------------
@login_required
@require_POST
def add_campaign_slab(request, product_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    cp = get_object_or_404(CampaignProduct, id=product_id)
    try:
        data = json.loads(request.body)
        threshold = _dec(data.get("threshold"))
        payout = _dec(data.get("payout"))
        label = (data.get("label") or "").strip()

        if not threshold or not payout or threshold <= 0 or payout <= 0:
            return JsonResponse({"error": "Threshold and payout must be positive."}, status=400)
        if CampaignSlab.objects.filter(campaign_product=cp, threshold=threshold).exists():
            return JsonResponse({"error": f"Slab at ₹{threshold} already exists."}, status=400)

        slab = CampaignSlab.objects.create(
            campaign_product=cp, threshold=threshold, payout=payout, label=label
        )
        return JsonResponse({
            "success": True,
            "slab": {
                "id": slab.id,
                "threshold": str(slab.threshold),
                "payout": str(slab.payout),
                "label": slab.label,
            },
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def update_campaign_slab(request, slab_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    slab = get_object_or_404(CampaignSlab, id=slab_id)
    try:
        data = json.loads(request.body)
        if "threshold" in data:
            threshold = _dec(data["threshold"])
            if not threshold or threshold <= 0:
                return JsonResponse({"error": "Threshold must be positive."}, status=400)
            slab.threshold = threshold
        if "payout" in data:
            payout = _dec(data["payout"])
            if not payout or payout <= 0:
                return JsonResponse({"error": "Payout must be positive."}, status=400)
            slab.payout = payout
        if "label" in data:
            slab.label = (data["label"] or "").strip()
        slab.save()
        return JsonResponse({"success": True, "message": "Slab updated."})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def delete_campaign_slab(request, slab_id):
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)
    slab = get_object_or_404(CampaignSlab, id=slab_id)
    slab.delete()
    return JsonResponse({"success": True, "message": "Slab deleted."})

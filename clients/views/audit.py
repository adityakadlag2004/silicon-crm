"""Read-only audit-log view. Admin-only."""
from django.core.paginator import Paginator
from django.shortcuts import render

from ..models import AuditLog
from ..permissions import admin_required


@admin_required
def audit_log(request):
    qs = AuditLog.objects.select_related("actor").all()

    # Filters
    action = (request.GET.get("action") or "").strip()
    target = (request.GET.get("target") or "").strip()
    actor = (request.GET.get("actor") or "").strip()
    if action:
        qs = qs.filter(action=action)
    if target:
        qs = qs.filter(target_model__iexact=target)
    if actor:
        qs = qs.filter(actor__username__icontains=actor)

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page"))

    # Build action options dynamically (only the ones that have rows).
    distinct_actions = list(
        AuditLog.objects.values_list("action", flat=True).distinct().order_by("action")
    )
    distinct_targets = list(
        AuditLog.objects.exclude(target_model="").values_list("target_model", flat=True).distinct().order_by("target_model")
    )

    return render(request, "audit_log.html", {
        "kpis": [
            {"label": "Log Entries", "value": paginator.count, "color": "#4338CA"},
            {"label": "Action Types", "value": len(distinct_actions), "color": "#0369A1"},
        ],
        "page_obj": page,
        "selected_action": action,
        "selected_target": target,
        "selected_actor": actor,
        "action_options": distinct_actions,
        "target_options": distinct_targets,
        "total_count": paginator.count,
    })

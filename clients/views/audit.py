"""Read-only audit-log view. Admin-only by default."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import render

from ..models import AuditLog


@login_required
def audit_log(request):
    emp = getattr(request.user, "employee", None)
    is_admin = request.user.is_superuser or (emp and emp.role == "admin")
    if not is_admin:
        return HttpResponseForbidden("Admins only.")

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
        "page_obj": page,
        "selected_action": action,
        "selected_target": target,
        "selected_actor": actor,
        "action_options": distinct_actions,
        "target_options": distinct_targets,
        "total_count": paginator.count,
    })

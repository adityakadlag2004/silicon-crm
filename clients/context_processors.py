from .models import ManagerAccessConfig


def manager_access(request):
    """Expose manager access config to templates.

    Returns ManagerAccessConfig.current() so templates can gate links.
    """
    return {"manager_access": ManagerAccessConfig.current()}


def nav(request):
    """One resolved permission set for the single sidebar definition in
    base.html — replaces the old per-role duplicated nav trees. Every menu
    item is gated on a flag here, so visibility lives in one place."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    from . import permissions

    is_admin = permissions.is_admin(user)
    is_manager = permissions.is_manager(user)
    is_employee = bool(getattr(user, "employee", None)) and not is_admin and not is_manager
    access = ManagerAccessConfig.current()

    return {"nav": {
        "is_admin": is_admin,
        "is_manager": is_manager,
        "is_employee": is_employee,
        # dashboard target + "my clients" (managers/employees only)
        "dashboard_admin": is_admin,
        "show_my_clients": not is_admin,
        "show_bulk_reassign": is_admin,
        # sales sub-items — admin always; managers by their granted flags
        "can_approve": permissions.can(user, "approve_sales"),
        "can_incentives": permissions.can(user, "manage_incentives"),
        "can_recalc": permissions.can(user, "recalc_points"),
        # analysis: admin + employees always; managers if granted
        "can_analysis": permissions.can(user, "client_analysis") or is_employee,
        # leads: admin + employees always; managers if granted
        "can_leads": is_admin or is_employee or (is_manager and access.allow_lead_management),
        # whole-section visibility
        "show_mf": is_admin,
        "show_settings": is_admin,
        "show_tasks_categories": is_admin,
        "show_reports_menu": is_manager and (
            access.allow_business_tracking or access.allow_employee_performance),
        "reports_performance": access.allow_employee_performance,
        "reports_business": access.allow_business_tracking,
    }}

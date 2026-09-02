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
        "show_admin_reports": is_admin,
        "show_settings": is_admin,
        "show_tasks_categories": is_admin,
        "show_reports_menu": is_manager and (
            access.allow_business_tracking or access.allow_employee_performance),
        "reports_performance": access.allow_employee_performance,
        "reports_business": access.allow_business_tracking,
    }}


# ── Breadcrumbs ──────────────────────────────────────────────────────────
# Every page gets a breadcrumb without touching its template or view. The
# section is derived from the URL name; a view that sets `crumbs` itself
# overrides this (view context beats context processors in Django).

_CRUMB_SECTIONS = [
    # (prefix or exact name, section label, section url name)
    ("client_kyc", "Clients", "all_clients"),
    ("client_", "Clients", "all_clients"),
    ("all_clients", "Clients", "all_clients"),
    ("my_clients", "Clients", "all_clients"),
    ("add_client", "Clients", "all_clients"),
    ("edit_client", "Clients", "all_clients"),
    ("map_client", "Clients", "all_clients"),
    ("bulk_reassign", "Clients", "all_clients"),
    ("family_", "Households", "family_list"),
    ("policy_", "Insurance Tracker", "policy_list"),
    ("claim_", "Claim Tracker", "claim_list"),
    ("meeting_", "Meetings", "meeting_list"),
    ("lead_", "Leads", "lead_management"),
    ("task_", "Tasks", "task_dashboard"),
    ("link_", "Links", "links_dashboard"),
    ("links_", "Links", "links_dashboard"),
    ("team_", "Team", "team_list"),
    ("sale", "Sales", "all_sales"),
    ("all_sales", "Sales", "all_sales"),
    ("add_sale", "Sales", "all_sales"),
    ("edit_sale", "Sales", "all_sales"),
    ("delete_sale", "Sales", "all_sales"),
    ("admin_add_sale", "Sales", "all_sales"),
    ("approve_sales", "Sales", "all_sales"),
    ("renewal", "Renewals", "all_renewals"),
    ("all_renewals", "Renewals", "all_renewals"),
    ("add_renewal", "Renewals", "all_renewals"),
    ("edit_renewal", "Renewals", "all_renewals"),
    ("delete_renewal", "Renewals", "all_renewals"),
    ("campaign", "Campaigns", "manage_campaigns"),
    ("manage_campaigns", "Campaigns", "manage_campaigns"),
    ("incentive", "Incentives", "manage_incentive_rules"),
    ("manage_incentive_rules", "Incentives", "manage_incentive_rules"),
    ("call_", "Calls", "my_call_followups"),
    ("my_call_followups", "Calls", "my_call_followups"),
    ("business_", "Reports", None),
    ("net_", "Reports", None),
    ("monthly_business_report", "Reports", None),
    ("employee_past_performance", "Reports", None),
    ("admin_past_performance", "Reports", None),
    ("admin_past_month_performance", "Reports", None),
    ("past_month_performance", "Reports", None),
    ("employee_performance", "Reports", None),
    ("financial_planner", "Reports", None),
    ("firm_settings", "Settings", None),
    ("product_management", "Settings", None),
    ("target_management", "Settings", None),
    ("employee_management", "Settings", None),
    ("audit_log", "Settings", None),
    ("employee_calendar", "Calendar", None),
]

# Page labels that don't read well when humanised from the URL name.
_CRUMB_LABELS = {
    "all_clients": "All Clients",
    "my_clients": "My Clients",
    "client_kyc_issues": "KYC Issues",
    "client_analysis": "Client Analysis",
    "client_profile": "Profile",
    "all_sales": "All Sales",
    "all_renewals": "All Renewals",
    "task_dashboard": "Dashboard",
    "task_my": "My Tasks",
    "task_delegated": "Delegated",
    "task_all": "All Tasks",
    "lead_management": "Lead Pipeline",
    "links_dashboard": "Business Links",
    "team_list": "Team Members",
    "my_call_followups": "Call Follow-ups",
}


def breadcrumbs(request):
    """Derive `crumbs` for the current URL. Views may override by setting it."""
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", None) if match else None
    if not name or name in ("admin_dashboard", "employee_dashboard", "login", "logout"):
        return {}

    section = section_url = None
    for prefix, label, url_name in _CRUMB_SECTIONS:
        if name == prefix or (prefix.endswith("_") and name.startswith(prefix)):
            section, section_url = label, url_name
            break
    if not section:
        return {}

    page = _CRUMB_LABELS.get(name) or name.replace("_", " ").title()
    # This page *is* the section's landing page — one crumb, not "Clients ›
    # All Clients".
    if page == section or section_url == name:
        return {"crumbs": [{"label": page}]}

    # Section first (linked when it has a landing page), then this page.
    crumbs = []
    if section_url and section_url != name:
        from django.urls import NoReverseMatch, reverse
        try:
            crumbs.append({"label": section, "url": reverse(f"clients:{section_url}")})
        except NoReverseMatch:
            crumbs.append({"label": section})
    else:
        crumbs.append({"label": section})
    crumbs.append({"label": page})
    return {"crumbs": crumbs}

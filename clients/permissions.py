"""Single source of truth for authorization.

Roles live on Employee.Role (admin / manager / employee). Manager feature
rights come from the ManagerAccessConfig singleton (edited on the Manager
Rights page). Everything that gates access anywhere in the app — web views,
app JSON APIs, templates via context — should resolve through this module,
so "who can do what" can be audited by reading one file.

Helpers take a `user` (request.user); superusers always count as admin.

    is_admin(user)                admin role or superuser
    is_admin_or_manager(user)     either elevated role
    can(user, "approve_sales")    admin, or manager with allow_approve_sales

Decorators (JSON-aware: API paths and XHR callers get a JSON 403 instead of
an HTML error page):

    @admin_required
    @admin_or_manager_required
    @can_required("approve_sales")
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse

from .models import Employee, ManagerAccessConfig

Role = Employee.Role


def get_employee(user):
    return getattr(user, "employee", None)


def role_of(user):
    emp = get_employee(user)
    return emp.role if emp else ""


def is_admin(user):
    return bool(getattr(user, "is_superuser", False) or role_of(user) == Role.ADMIN)


def is_manager(user):
    return role_of(user) == Role.MANAGER


def is_admin_or_manager(user):
    return is_admin(user) or is_manager(user)


def manager_access(user):
    """The ManagerAccessConfig singleton if `user` is a manager, else None.
    (Views pass this to templates to show/hide manager-only menu items.)"""
    return ManagerAccessConfig.current() if is_manager(user) else None


def can(user, flag):
    """Feature-level check: admins always may; managers if the matching
    `allow_<flag>` is enabled; employees never. Unknown flags are denied."""
    if is_admin(user):
        return True
    if is_manager(user):
        return bool(getattr(ManagerAccessConfig.current(), f"allow_{flag}", False))
    return False


def _wants_json(request):
    return (
        request.path.startswith("/clients/api/")
        or request.content_type == "application/json"
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


def _forbid(request, message):
    if _wants_json(request):
        return JsonResponse({"ok": False, "success": False, "error": message}, status=403)
    return HttpResponseForbidden(message)


def _gate(check, message):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(request, *args, **kwargs):
            if not check(request.user):
                return _forbid(request, message)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


# _gate already returns a decorator, so these are the decorators — no wrapper
# function needed to hand the view along.
admin_required = _gate(is_admin, "Admin access required.")
admin_or_manager_required = _gate(is_admin_or_manager, "Manager or admin access required.")


def can_required(flag):
    return _gate(
        lambda user: can(user, flag),
        "You do not have permission to access this feature.",
    )

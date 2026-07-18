"""Authentication views: login, logout."""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache


LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _client_ip(request):
    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (request.META.get("REMOTE_ADDR") or "unknown").strip()


def _login_lockout_key(request, username):
    return f"auth:login:fail:{_client_ip(request)}:{(username or '').strip().lower()}"


def _dashboard_redirect(user):
    """Role-based landing page, or None if the user has no role mapping."""
    emp = getattr(user, "employee", None)
    role = emp.role.lower() if emp and emp.role else None
    if role == "admin":
        return redirect("clients:admin_dashboard")
    if role in ("manager", "employee"):
        return redirect("clients:employee_dashboard")
    if user.is_superuser or user.is_staff:
        # Superusers/staff created via createsuperuser have no Employee
        # record but must still be able to log in.
        return redirect("clients:admin_dashboard")
    return None


def login_view(request):
    # Already signed in (e.g. the Android app reopening on its start URL) —
    # go straight to the dashboard instead of showing the login form again.
    if request.user.is_authenticated:
        target = _dashboard_redirect(request.user)
        if target is not None:
            return target

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        throttle_key = _login_lockout_key(request, username)
        failed_attempts = cache.get(throttle_key, 0)

        if failed_attempts >= LOGIN_MAX_ATTEMPTS:
            messages.error(request, "Too many failed login attempts. Try again in 15 minutes.")
            return render(request, "login.html")

        user = authenticate(request, username=username, password=password)
        if user:
            cache.delete(throttle_key)
            login(request, user)
            target = _dashboard_redirect(user)
            if target is not None:
                return target
            messages.error(request, "No employee role mapped. Contact an administrator.")
        else:
            cache.set(throttle_key, failed_attempts + 1, LOGIN_LOCKOUT_SECONDS)
            messages.error(request, "Invalid username or password")

    return render(request, "login.html")


@login_required
def logout_view(request):
    logout(request)
    return redirect("clients:login")


@login_required
def getting_started(request):
    """Day-1 checklist for new team members — the same for every role, with
    admin-only steps gated. Linked from the dashboard; safe for anyone."""
    from .. import permissions

    return render(request, "getting_started.html", {
        "page_title": "Getting Started",
        "is_admin": permissions.is_admin(request.user),
    })

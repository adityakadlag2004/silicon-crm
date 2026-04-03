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


def login_view(request):
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

            # Redirect based on role
            if hasattr(user, "employee"):
                role = user.employee.role.lower()
                if role == "admin":
                    return redirect("clients:admin_dashboard")
                elif role == "manager":
                    return redirect("clients:employee_dashboard")
                elif role == "employee":
                    return redirect("clients:employee_dashboard")
            else:
                messages.error(request, "No employee role mapped.")
        else:
            cache.set(throttle_key, failed_attempts + 1, LOGIN_LOCKOUT_SECONDS)
            messages.error(request, "Invalid username or password")

    return render(request, "login.html")


@login_required
def logout_view(request):
    logout(request)
    return redirect("clients:login")

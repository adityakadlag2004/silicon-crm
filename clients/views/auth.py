"""Authentication views: login, logout."""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
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
            messages.error(request, "Invalid username or password")

    return render(request, "login.html")


@login_required
def logout_view(request):
    logout(request)
    return redirect("clients:login")

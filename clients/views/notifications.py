"""Notification JSON endpoints for admin dashboard polling."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse

from ..models import Notification


@login_required
def notifications_json(request):
    if not getattr(request.user, "employee", None) or request.user.employee.role != "admin":
        return HttpResponseForbidden("Admins only")
    notes = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:20]
    data = [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "link": n.link,
            "created_at": n.created_at.strftime("%b %d, %I:%M %p"),
            "is_read": n.is_read,
        }
        for n in notes
    ]
    unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({"notifications": data, "unread": unread_count})


@login_required
def notifications_mark_all_read(request):
    if not getattr(request.user, "employee", None) or request.user.employee.role != "admin":
        return HttpResponseForbidden("Admins only")
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return JsonResponse({"status": "ok"})


@login_required
def notifications_clear(request):
    if not getattr(request.user, "employee", None) or request.user.employee.role != "admin":
        return HttpResponseForbidden("Admins only")
    Notification.objects.filter(recipient=request.user).delete()
    return JsonResponse({"status": "ok"})

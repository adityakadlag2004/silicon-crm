"""Notification JSON endpoints for admin dashboard polling."""
import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST

from ..models import Notification, PushDevice


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
@require_POST
def push_register(request):
    """Called by the Android app after login to register its FCM token."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)

    token = (data.get("token") or "").strip()
    if not token or len(token) > 512:
        return JsonResponse({"ok": False, "error": "token required"}, status=400)
    platform = (data.get("platform") or "android").strip()[:20]

    # Token is unique per device: if it moved to another user (shared phone,
    # relogin), reassign it so pushes follow the currently logged-in user.
    device, _created = PushDevice.objects.update_or_create(
        token=token, defaults={"user": request.user, "platform": platform}
    )
    return JsonResponse({"ok": True})


@login_required
@require_POST
def push_unregister(request):
    """Called by the app on logout so the device stops receiving pushes."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid payload"}, status=400)
    token = (data.get("token") or "").strip()
    if token:
        PushDevice.objects.filter(token=token, user=request.user).delete()
    return JsonResponse({"ok": True})


@login_required
def notifications_clear(request):
    if not getattr(request.user, "employee", None) or request.user.employee.role != "admin":
        return HttpResponseForbidden("Admins only")
    Notification.objects.filter(recipient=request.user).delete()
    return JsonResponse({"status": "ok"})

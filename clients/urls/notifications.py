"""Web notifications + push device registration."""
from django.urls import path

from ..views import notifications

urlpatterns = [
    path("notifications/json/", notifications.notifications_json, name="notifications_json"),
    path("notifications/mark-all-read/", notifications.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("notifications/clear/", notifications.notifications_clear, name="notifications_clear"),
    path("api/push/register/", notifications.push_register, name="push_register"),
    path("api/push/unregister/", notifications.push_unregister, name="push_unregister"),
]

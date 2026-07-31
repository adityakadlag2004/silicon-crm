"""Call tracking: app sync endpoints + follow-up pages + analytics."""
from django.urls import path

from ..views import calls

urlpatterns = [
    path("api/calls/config/", calls.call_config, name="call_config"),
    path("api/calls/sync/", calls.calls_sync, name="calls_sync"),
    path("api/calls/followup/", calls.call_followup_create, name="call_followup_create"),
    path("api/calls/context/", calls.call_context, name="call_context"),
    path("api/calls/close/", calls.call_close, name="call_close"),
    path("calls/followups/", calls.my_call_followups, name="my_call_followups"),
    path("calls/followups/<int:followup_id>/update/", calls.call_followup_update, name="call_followup_update"),
    path("calls/analytics/", calls.call_analytics, name="call_analytics"),
]

"""Common calendar page, unified feeds, event CRUD."""
from django.urls import path

from ..views import calendar_views

urlpatterns = [
    path("calendar/my-calendar/", calendar_views.employee_calendar, name="employee_calendar"),
    path("calendar/view/", calendar_views.employee_calendar_page, name="employee_calendar_page"),
    path("calendar/events-json/", calendar_views.calendar_events_json, name="calendar_events_json"),
    path("calendar/agenda-json/", calendar_views.dashboard_agenda_json, name="dashboard_agenda_json"),
    path("calendar/update-event/", calendar_views.update_calendar_event, name="update_calendar_event"),
    path("calendar/create-event/", calendar_views.create_calendar_event, name="create_calendar_event"),
    path("calendar/delete-event/", calendar_views.delete_calendar_event, name="delete_calendar_event"),
    path("calendar/update-event-details/", calendar_views.update_calendar_event_details, name="update_calendar_event_details"),
    path("calendar/mark-done/<int:event_id>/", calendar_views.mark_done, name="mark_done"),
    path("calendar/reschedule/<int:event_id>/", calendar_views.reschedule_event, name="reschedule"),
    path("calendar/skip/<int:event_id>/", calendar_views.skip_event, name="skip"),
]

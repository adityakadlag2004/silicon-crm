"""Lead Pipeline (stages, follow-ups, conversion)."""
from django.urls import path

from ..views import leads

urlpatterns = [
    path("leads/", leads.lead_management, name="lead_management"),
    path("leads/stats/team/", leads.lead_progress_overview_admin, name="lead_progress_admin"),
    path("leads/stats/mine/", leads.lead_progress_overview_employee, name="lead_progress_employee"),
    path("leads/bulk-import/", leads.lead_bulk_import, name="lead_bulk_import"),
    path("leads/stage/<str:stage>/", leads.lead_list_by_stage, name="lead_stage_list"),
    path("leads/new/", leads.lead_create, name="lead_create"),
    path("leads/<int:lead_id>/complete/", leads.lead_mark_complete, name="lead_mark_complete"),
    path("leads/<int:lead_id>/discard/", leads.lead_discard, name="lead_discard"),
    path("leads/<int:lead_id>/undiscard/", leads.lead_undiscard, name="lead_undiscard"),
    path("leads/<int:lead_id>/convert/", leads.lead_convert_to_client, name="lead_convert_to_client"),
    path("leads/followup/<int:followup_id>/done/", leads.lead_followup_done, name="lead_followup_done"),
    path("leads/followup/<int:followup_id>/reschedule/", leads.lead_followup_reschedule, name="lead_followup_reschedule"),
    path("leads/<int:lead_id>/add-followup/", leads.lead_add_followup, name="lead_add_followup"),
    path("leads/<int:lead_id>/add-remark/", leads.lead_add_remark, name="lead_add_remark"),
    path("leads/<int:lead_id>/", leads.lead_detail, name="lead_detail"),
    path("leads/<int:lead_id>/edit/", leads.lead_update, name="lead_update"),
]

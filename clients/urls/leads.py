"""Lead Pipeline (SPANCO stages, follow-ups, conversion)."""
from django.urls import path

from ..views import leads

urlpatterns = [
    path("leads/", leads.lead_management, name="lead_management"),
    path("leads/board/", leads.lead_board, name="lead_board"),
    path("leads/pipeline/", leads.lead_pipeline_report, name="lead_pipeline_report"),
    path("leads/bulk-import/", leads.lead_bulk_import, name="lead_bulk_import"),
    path("leads/new/", leads.lead_create, name="lead_create"),
    path("leads/<int:lead_id>/stage/", leads.lead_set_stage, name="lead_set_stage"),
    path("leads/<int:lead_id>/discard/", leads.lead_discard, name="lead_discard"),
    path("leads/<int:lead_id>/undiscard/", leads.lead_undiscard, name="lead_undiscard"),
    path("leads/<int:lead_id>/convert/", leads.lead_convert_to_client, name="lead_convert_to_client"),
    path("leads/<int:lead_id>/add-followup/", leads.lead_add_followup, name="lead_add_followup"),
    path("leads/<int:lead_id>/add-remark/", leads.lead_add_remark, name="lead_add_remark"),
    path("leads/<int:lead_id>/", leads.lead_detail, name="lead_detail"),
    path("leads/<int:lead_id>/edit/", leads.lead_update, name="lead_update"),
]

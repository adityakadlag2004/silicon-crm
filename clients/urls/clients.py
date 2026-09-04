"""Client CRUD, profile, KYC issues, merge, reassign, analysis."""
from django.urls import path

from ..views import clients_views, kyc

urlpatterns = [
    path("clients/<int:client_id>/edit/", clients_views.edit_client, name="edit_client"),
    path("clients/<int:client_id>/profile/", clients_views.client_profile, name="client_profile"),
    path("clients/<int:client_id>/drive-folder/", clients_views.client_drive_folder, name="client_drive_folder"),
    path("analysis/", clients_views.client_analysis, name="client_analysis"),
    path("all/", clients_views.all_clients, name="all_clients"),
    path("families/", clients_views.family_list, name="family_list"),
    path("families/<int:family_id>/", clients_views.family_detail, name="family_detail"),
    path("add/", clients_views.add_client, name="add_client"),
    path("my/", clients_views.my_clients, name="my_clients"),
    path("search/", clients_views.search_clients, name="search_clients"),
    path("<int:client_id>/map/", clients_views.map_client, name="map_client"),
    path("<int:client_id>/reassign/", clients_views.client_reassign_view, name="reassign"),
    path("reassign-bulk/", clients_views.bulk_reassign_view, name="bulk_reassign"),
    path("clients/kyc-issues/", kyc.client_kyc_issues, name="client_kyc_issues"),
    path("clients/<int:client_id>/kyc-pan/", kyc.client_kyc_update_pan, name="client_kyc_update_pan"),
    path("clients/<int:client_id>/kyc-dob/", kyc.client_kyc_update_dob, name="client_kyc_update_dob"),
    path("clients/merge/", kyc.client_merge_view, name="client_merge"),
    path("clients/bulk-merge/", kyc.client_bulk_merge, name="client_bulk_merge"),
    path("clients/<int:client_id>/safe-delete/", kyc.client_safe_delete, name="client_safe_delete"),
]

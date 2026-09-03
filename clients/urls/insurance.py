"""Insurance Tracker and Claim Tracker URLs."""
from django.urls import path

from ..views import insurance

urlpatterns = [
    path("insurance/", insurance.policy_list, name="policy_list"),
    path("insurance/<int:policy_id>/", insurance.policy_detail, name="policy_detail"),
    path("claims/", insurance.claim_list, name="claim_list"),
    path("claims/raise/", insurance.raise_claim, name="raise_claim"),
    path("claims/raise/<int:policy_id>/", insurance.raise_claim, name="raise_claim_for_policy"),
    path("claims/<int:claim_id>/", insurance.claim_detail, name="claim_detail"),
    path("claims/<int:claim_id>/status/", insurance.claim_update_status, name="claim_update_status"),
    path("claims/<int:claim_id>/note/", insurance.claim_add_note, name="claim_add_note"),
    path("claims/<int:claim_id>/reminder/", insurance.claim_add_reminder, name="claim_add_reminder"),
    path("claims/<int:claim_id>/document/upload/", insurance.claim_upload_document, name="claim_upload_document"),
    path("claims/document/<int:doc_id>/download/", insurance.claim_document_download, name="claim_document_download"),
    path("claims/document/<int:doc_id>/delete/", insurance.claim_delete_document, name="claim_delete_document"),
    path("api/client/<int:client_id>/policies/", insurance.client_policies_json,
         name="client_policies_json"),
]

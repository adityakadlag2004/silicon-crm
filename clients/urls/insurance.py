"""Insurance Tracker, Claim Tracker and Meetings URLs."""
from django.urls import path

from ..views import insurance

urlpatterns = [
    path("insurance/", insurance.policy_list, name="policy_list"),
    path("insurance/<int:policy_id>/", insurance.policy_detail, name="policy_detail"),
    path("claims/", insurance.claim_list, name="claim_list"),
    path("claims/<int:claim_id>/", insurance.claim_detail, name="claim_detail"),
    path("meetings/", insurance.meeting_list, name="meeting_list"),
    path("api/client/<int:client_id>/policies/", insurance.client_policies_json,
         name="client_policies_json"),
]

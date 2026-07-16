"""Mutual funds: CAMS/KFintech RTA feeds, folios, transactions."""
from django.urls import path

from ..views import mf

urlpatterns = [
    path("mf/", mf.mf_dashboard, name="mf_dashboard"),
    path("mf/upload/", mf.mf_upload, name="mf_upload"),
    path("mf/fetch-now/", mf.mf_fetch_now, name="mf_fetch_now"),
    path("mf/relink/", mf.mf_relink, name="mf_relink"),
    path("mf/arn/save/", mf.mf_arn_save, name="mf_arn_save"),
    path("mf/arn/<int:account_id>/delete/", mf.mf_arn_delete, name="mf_arn_delete"),
    path("mf/folios/", mf.mf_folios, name="mf_folios"),
    path("mf/folios/<int:folio_id>/link/", mf.mf_folio_link, name="mf_folio_link"),
    path("mf/transactions/", mf.mf_transactions, name="mf_transactions"),
]

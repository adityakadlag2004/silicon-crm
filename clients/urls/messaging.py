"""Bulk WhatsApp tools."""
from django.urls import path

from ..views import messaging

urlpatterns = [
    path("bulk_whatsapp/", messaging.bulk_whatsapp, name="bulk_whatsapp"),
    path("wa-preview/", messaging.wa_preview_page, name="wa_preview_page"),
    path("wa-preview-csv/", messaging.wa_preview_csv, name="wa_preview_csv"),
]

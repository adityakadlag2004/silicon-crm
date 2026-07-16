"""Renewals CRUD."""
from django.urls import path

from ..views import renewal_views

urlpatterns = [
    path("renewals/add/", renewal_views.add_renewal, name="add_renewal"),
    path("renewals/<int:client_id>/add/", renewal_views.add_renewal, name="add_renewal_for_client"),
    path("renewals/all/", renewal_views.all_renewals, name="all_renewals"),
    path("renewals/<int:renewal_id>/edit/", renewal_views.edit_renewal, name="edit_renewal"),
    path("renewals/<int:renewal_id>/delete/", renewal_views.delete_renewal, name="delete_renewal"),
    path("renewals/quick-add-client/", renewal_views.quick_add_client_for_renewal, name="quick_add_client_for_renewal"),
]

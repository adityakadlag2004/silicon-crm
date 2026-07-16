"""Target & special campaigns builder."""
from django.urls import path

from ..views import campaigns

urlpatterns = [
    path("campaigns/manage/", campaigns.manage_campaigns, name="manage_campaigns"),
    path("campaigns/add/", campaigns.add_campaign, name="add_campaign"),
    path("campaigns/<int:campaign_id>/update/", campaigns.update_campaign, name="update_campaign"),
    path("campaigns/<int:campaign_id>/delete/", campaigns.delete_campaign, name="delete_campaign"),
    path("campaigns/<int:campaign_id>/product/add/", campaigns.add_campaign_product, name="add_campaign_product"),
    path("campaigns/product/<int:product_id>/update/", campaigns.update_campaign_product, name="update_campaign_product"),
    path("campaigns/product/<int:product_id>/delete/", campaigns.delete_campaign_product, name="delete_campaign_product"),
    path("campaigns/product/<int:product_id>/slab/add/", campaigns.add_campaign_slab, name="add_campaign_slab"),
    path("campaigns/slab/<int:slab_id>/update/", campaigns.update_campaign_slab, name="update_campaign_slab"),
    path("campaigns/slab/<int:slab_id>/delete/", campaigns.delete_campaign_slab, name="delete_campaign_slab"),
]

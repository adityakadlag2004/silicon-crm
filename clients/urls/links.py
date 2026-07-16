"""Business Links module."""
from django.urls import path

from ..views import links

urlpatterns = [
    path("links/", links.links_dashboard, name="links_dashboard"),
    path("links/categories/", links.link_categories, name="link_categories"),
    path("links/categories/create/", links.link_category_create, name="link_category_create"),
    path("links/category/<int:cat_id>/", links.links_category, name="links_category"),
    path("links/create/", links.link_create, name="link_create"),
    path("links/<int:link_id>/update/", links.link_update, name="link_update"),
    path("links/<int:link_id>/delete/", links.link_delete, name="link_delete"),
    path("links/<int:link_id>/favorite/", links.link_favorite, name="link_favorite"),
    path("links/<int:link_id>/move/", links.link_move, name="link_move"),
]

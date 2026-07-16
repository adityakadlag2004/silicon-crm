"""Team management (roster, add/edit, activate, reset password)."""
from django.urls import path

from ..views import team

urlpatterns = [
    path("team/", team.team_list, name="team_list"),
    path("team/add/", team.team_add, name="team_add"),
    path("team/<int:employee_id>/", team.team_detail, name="team_detail"),
    path("team/<int:employee_id>/edit/", team.team_edit, name="team_edit"),
    path("team/<int:employee_id>/toggle-status/", team.team_toggle_status, name="team_toggle_status"),
    path("team/<int:employee_id>/delete/", team.team_delete, name="team_delete"),
    path("team/<int:employee_id>/reset-password/", team.team_reset_password, name="team_reset_password"),
]

"""clients URL configuration, split by domain.

Each module holds the path() entries for one domain and they are
assembled here under the single "clients" namespace, so every URL
name stays `clients:<name>` and every path is unchanged.
"""
from . import auth, clients, dashboards, team, reports, notifications, calls, tasks, links, api_app, sales, renewals, leads, campaigns, calendar, messaging, mf

app_name = "clients"

urlpatterns = [
    *auth.urlpatterns,
    *clients.urlpatterns,
    *dashboards.urlpatterns,
    *team.urlpatterns,
    *reports.urlpatterns,
    *notifications.urlpatterns,
    *calls.urlpatterns,
    *tasks.urlpatterns,
    *links.urlpatterns,
    *api_app.urlpatterns,
    *sales.urlpatterns,
    *renewals.urlpatterns,
    *leads.urlpatterns,
    *campaigns.urlpatterns,
    *calendar.urlpatterns,
    *messaging.urlpatterns,
    *mf.urlpatterns,
]

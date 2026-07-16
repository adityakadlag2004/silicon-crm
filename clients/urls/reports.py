"""Business reports & analytics."""
from django.urls import path

from ..views import reports

urlpatterns = [
    path("admin/past-performance/", reports.admin_past_performance, name="admin_past_performance"),
    path("admin/past-performance/<int:year>/<int:month>/", reports.admin_past_month_performance, name="admin_past_month_performance"),
    path("past-performance/", reports.employee_past_performance, name="employee_past_performance"),
    path("past-performance/<int:year>/<int:month>/", reports.past_month_performance, name="past_month_performance"),
    path("reports/business-overview/", reports.business_overview, name="business_overview"),
    path("reports/monthly-business/", reports.monthly_business_report, name="monthly_business_report"),
    path("reports/business-analytics/", reports.business_analytics, name="business_analytics"),
]

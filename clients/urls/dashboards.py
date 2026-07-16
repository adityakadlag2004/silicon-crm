"""Admin + employee dashboards, admin pages, past performance, audit log."""
from django.urls import path

from ..views import audit, dashboards

urlpatterns = [
    path("dashboard/admin/", dashboards.admin_dashboard, name="admin_dashboard"),
    path("admin/employees/", dashboards.employee_management, name="employee_management"),
    path("admin/firm-settings/", dashboards.firm_settings_page, name="firm_settings"),
    path("admin/products/", dashboards.product_management_page, name="product_management"),
    path("admin/targets/", dashboards.target_management, name="target_management"),
    path("admin/audit-log/", audit.audit_log, name="audit_log"),
    path("dashboard/employee/", dashboards.employee_dashboard, name="employee_dashboard"),
    path("sales/performance/", dashboards.employee_performance, name="employee_performance"),
    path("dashboard/net-business/", dashboards.net_business, name="net_business"),
    path("dashboard/net-sip/", dashboards.net_sip, name="net_sip"),
]

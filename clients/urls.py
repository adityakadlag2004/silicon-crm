# clients/urls.py
from django.urls import path
from . import views



urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    
    path("dashboard/admin/", views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/employee/", views.employee_dashboard, name="employee_dashboard"),
    

    # New
    # sales CRUD
    path("sales/add/", views.add_sale, name="add_sale"),
    path("sales/admin_add/", views.admin_add_sale, name="admin_add_sale"),
    path("sales/<int:sale_id>/edit/", views.edit_sale, name="edit_sale"),
    path("sales/<int:sale_id>/delete/", views.delete_sale, name="delete_sale"),
    path("sales/recalc/", views.recalc_points, name="recalc_points"),
    path("sales/all/", views.all_sales, name="all_sales"),
    path("sales/admin_add/", views.admin_add_sale, name="admin_add_sale"),

    path("analysis/", views.client_analysis, name="client_analysis"),

    # Admin
    path("clients/all/", views.all_clients, name="all_clients"),
    path("clients/add/", views.add_client, name="add_client"),
    path("clients/my/", views.my_clients, name="my_clients"),
    path("clients/search/", views.search_clients, name="search_clients"),
    path("clients/<int:client_id>/map/", views.map_client, name="map_client"),
    
    
    
    path("incentives/manage/", views.manage_incentive_rules, name="manage_incentive_rules"),
    
    
    path("past-performance/", views.employee_past_performance, name="employee_past_performance"),
    path("past-performance/<int:year>/<int:month>/", views.past_month_performance, name="past_month_performance"),
    


]

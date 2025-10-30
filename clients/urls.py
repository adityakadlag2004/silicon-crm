# clients/urls.py
from django.urls import path
from . import views

app_name = "clients"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("clients/<int:client_id>/edit/", views.edit_client, name="edit_client"),
    
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
    path("all/", views.all_clients, name="all_clients"),
    path("add/", views.add_client, name="add_client"),
    path("my/", views.my_clients, name="my_clients"),
    path("search/", views.search_clients, name="search_clients"),
    path("<int:client_id>/map/", views.map_client, name="map_client"),
    

    path("incentives/manage/", views.manage_incentive_rules, name="manage_incentive_rules"),
    
    
    path("past-performance/", views.employee_past_performance, name="employee_past_performance"),
    path("past-performance/<int:year>/<int:month>/", views.past_month_performance, name="past_month_performance"),
    

    # calling
    path("calling/upload/", views.upload_list, name="upload_list"),
    path("calling/admin-lists/", views.admin_lists, name="admin_lists"),
    path("calling/employee-lists/", views.employee_lists, name="employee_lists"),
    path("calling/workspace/<int:list_id>/", views.calling_workspace, name="callingworkspace"),
    path("calling/admin-list/<int:list_id>/", views.admin_list_detail, name="admin_list_detail"),
    path("calling/delete-list/<int:list_id>/", views.delete_calling_list, name="delete_calling_list"),
    path("calling/log-result/<int:prospect_id>/", views.log_result, name="log_result"),
    path("calling/add-followup/<int:prospect_id>/", views.add_followup, name="add_followup"),


    # calendar
    path("calendar/my-calendar/", views.employee_calendar, name="employee_calendar"),
    path("calendar/view/", views.employee_calendar_page, name="employee_calendar"),
    path("calendar/events-json/", views.calendar_events_json, name="calendar_events_json"),
    path("calendar/update-event/", views.update_calendar_event, name="update_calendar_event"),
    path("calendar/mark-done/<int:event_id>/", views.mark_done, name="mark_done"),
    path("calendar/reschedule/<int:event_id>/", views.reschedule_event, name="reschedule"),
    path("calendar/skip/<int:event_id>/", views.skip_event, name="skip"),

    # bulk messaging
    path("bulk_whatsapp/", views.bulk_whatsapp, name="bulk_whatsapp"),


    
    
    




]

# clients/urls.py
from django.urls import path
from . import views

app_name = "clients"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("clients/<int:client_id>/edit/", views.edit_client, name="edit_client"),
    path("clients/<int:client_id>/profile/", views.client_profile, name="client_profile"),
    path("clients/<int:client_id>/drive-folder/", views.client_drive_folder, name="client_drive_folder"),
    
    path("dashboard/admin/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/employees/", views.employee_management, name="employee_management"),
    path("admin/firm-settings/", views.firm_settings_page, name="firm_settings"),
    path("admin/products/", views.product_management_page, name="product_management"),
    path("admin/audit-log/", views.audit_log, name="audit_log"),
    path("dashboard/employee/", views.employee_dashboard, name="employee_dashboard"),

    # Team management
    path("team/", views.team_list, name="team_list"),
    path("team/add/", views.team_add, name="team_add"),
    path("team/<int:employee_id>/", views.team_detail, name="team_detail"),
    path("team/<int:employee_id>/edit/", views.team_edit, name="team_edit"),
    path("team/<int:employee_id>/toggle-status/", views.team_toggle_status, name="team_toggle_status"),
    path("team/<int:employee_id>/delete/", views.team_delete, name="team_delete"),
    path("team/<int:employee_id>/reset-password/", views.team_reset_password, name="team_reset_password"),

  # ----- Admin past performance -----
    path("admin/past-performance/", views.admin_past_performance, name="admin_past_performance"),
    path("admin/past-performance/<int:year>/<int:month>/", views.admin_past_month_performance, name="admin_past_month_performance"),

    # Notifications
    path("notifications/json/", views.notifications_json, name="notifications_json"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("notifications/clear/", views.notifications_clear, name="notifications_clear"),


    # New
    # sales CRUD
    path("sales/add/", views.add_sale, name="add_sale"),
    path("sales/admin_add/", views.admin_add_sale, name="admin_add_sale"),
    path("sales/approve/", views.approve_sales, name="approve_sales"),
    path("sales/<int:sale_id>/edit/", views.edit_sale, name="edit_sale"),
    path("sales/<int:sale_id>/delete/", views.delete_sale, name="delete_sale"),
    path("sales/recalc/", views.recalc_points, name="recalc_points"),
    path("sales/all/", views.all_sales, name="all_sales"),

    # renewals CRUD
    path("renewals/add/", views.add_renewal, name="add_renewal"),
    path("renewals/<int:client_id>/add/", views.add_renewal, name="add_renewal_for_client"),
    path("renewals/all/", views.all_renewals, name="all_renewals"),
    path("renewals/<int:renewal_id>/edit/", views.edit_renewal, name="edit_renewal"),
    path("renewals/<int:renewal_id>/delete/", views.delete_renewal, name="delete_renewal"),
    path("renewals/quick-add-client/", views.quick_add_client_for_renewal, name="quick_add_client_for_renewal"),

    path("leads/", views.lead_management, name="lead_management"),
    path("leads/stats/team/", views.lead_progress_overview_admin, name="lead_progress_admin"),
    path("leads/stats/mine/", views.lead_progress_overview_employee, name="lead_progress_employee"),
    # Lead management
    path("leads/bulk-import/", views.lead_bulk_import, name="lead_bulk_import"),
    path("leads/stage/<str:stage>/", views.lead_list_by_stage, name="lead_stage_list"),
    path("leads/new/", views.lead_create, name="lead_create"),
    path("leads/<int:lead_id>/complete/", views.lead_mark_complete, name="lead_mark_complete"),
    path("leads/<int:lead_id>/discard/", views.lead_discard, name="lead_discard"),
    path("leads/<int:lead_id>/undiscard/", views.lead_undiscard, name="lead_undiscard"),
    path("leads/<int:lead_id>/convert/", views.lead_convert_to_client, name="lead_convert_to_client"),
    path("leads/followup/<int:followup_id>/done/", views.lead_followup_done, name="lead_followup_done"),
    path("leads/followup/<int:followup_id>/reschedule/", views.lead_followup_reschedule, name="lead_followup_reschedule"),
    path("leads/followups-api/", views.lead_followups_api, name="lead_followups_api"),
    path("leads/<int:lead_id>/add-followup/", views.lead_add_followup, name="lead_add_followup"),
    path("leads/<int:lead_id>/add-remark/", views.lead_add_remark, name="lead_add_remark"),
    path("leads/<int:lead_id>/", views.lead_detail, name="lead_detail"),
    path("leads/<int:lead_id>/edit/", views.lead_update, name="lead_update"),

    # Lead Records — spreadsheet-style lead tracking
    path("leads/sheets/", views.lead_sheets_list, name="lead_sheets"),
    path("leads/sheets/create/", views.lead_sheet_create, name="lead_sheet_create"),
    path("leads/sheets/<int:sheet_id>/", views.lead_sheet_detail, name="lead_sheet_detail"),
    path("leads/sheets/<int:sheet_id>/access/", views.lead_sheet_access, name="lead_sheet_access"),
    path("leads/sheets/<int:sheet_id>/archive/", views.lead_sheet_archive, name="lead_sheet_archive"),
    path("leads/sheets/<int:sheet_id>/columns/add/", views.lead_sheet_column_add, name="lead_sheet_column_add"),
    path("leads/sheets/<int:sheet_id>/columns/<int:column_id>/delete/", views.lead_sheet_column_delete, name="lead_sheet_column_delete"),
    path("leads/sheets/<int:sheet_id>/records/add/", views.lead_sheet_record_add, name="lead_sheet_record_add"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/update/", views.lead_sheet_record_update, name="lead_sheet_record_update"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/delete/", views.lead_sheet_record_delete, name="lead_sheet_record_delete"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/convert/", views.lead_sheet_record_convert, name="lead_sheet_record_convert"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/", views.lead_sheet_record_detail, name="lead_sheet_record_detail"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/followups/add/", views.lead_sheet_followup_add, name="lead_sheet_followup_add"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/followups/<int:followup_id>/done/", views.lead_sheet_followup_done, name="lead_sheet_followup_done"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/followups/<int:followup_id>/delete/", views.lead_sheet_followup_delete, name="lead_sheet_followup_delete"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/tags/add/", views.lead_sheet_record_tag_add, name="lead_sheet_record_tag_add"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/tags/remove/", views.lead_sheet_record_tag_remove, name="lead_sheet_record_tag_remove"),
    path("leads/sheets/<int:sheet_id>/records/<int:record_id>/assign/", views.lead_sheet_record_assign, name="lead_sheet_record_assign"),
    path("leads/sheets/<int:sheet_id>/distribute/", views.lead_sheet_distribute, name="lead_sheet_distribute"),
    path("leads/sheets/<int:sheet_id>/import-csv/", views.lead_sheet_import_csv, name="lead_sheet_import_csv"),
  # Employee performance
  path("sales/performance/", views.employee_performance, name="employee_performance"),
  path("dashboard/net-business/", views.net_business, name="net_business"),
  path("dashboard/net-sip/", views.net_sip, name="net_sip"),
    path("analysis/", views.client_analysis, name="client_analysis"),

    # Admin
    path("all/", views.all_clients, name="all_clients"),
    path("add/", views.add_client, name="add_client"),
    path("my/", views.my_clients, name="my_clients"),
    path("search/", views.search_clients, name="search_clients"),
    path("<int:client_id>/map/", views.map_client, name="map_client"),
    

    path("incentives/manage/", views.manage_incentive_rules, name="manage_incentive_rules"),
    path("incentives/rule/add/", views.add_incentive_rule, name="add_incentive_rule"),
    path("incentives/rule/<int:rule_id>/update/", views.update_incentive_rule, name="update_incentive_rule"),
    path("incentives/rule/<int:rule_id>/delete/", views.delete_incentive_rule, name="delete_incentive_rule"),
    path("incentives/rule/<int:rule_id>/slab/add/", views.add_incentive_slab, name="add_incentive_slab"),
    path("incentives/slab/<int:slab_id>/update/", views.update_incentive_slab, name="update_incentive_slab"),
    path("incentives/slab/<int:slab_id>/delete/", views.delete_incentive_slab, name="delete_incentive_slab"),
    
    
    path("past-performance/", views.employee_past_performance, name="employee_past_performance"),
    path("past-performance/<int:year>/<int:month>/", views.past_month_performance, name="past_month_performance"),
    

    # calling component removed — see migration for table drops


    # calendar
    path("calendar/my-calendar/", views.employee_calendar, name="employee_calendar"),
    path("calendar/view/", views.employee_calendar_page, name="employee_calendar_page"),
    path("calendar/events-json/", views.calendar_events_json, name="calendar_events_json"),
    path("calendar/update-event/", views.update_calendar_event, name="update_calendar_event"),
  path("calendar/create-event/", views.create_calendar_event, name="create_calendar_event"),
  path("calendar/delete-event/", views.delete_calendar_event, name="delete_calendar_event"),
  path("calendar/update-event-details/", views.update_calendar_event_details, name="update_calendar_event_details"),
    path("calendar/mark-done/<int:event_id>/", views.mark_done, name="mark_done"),
    path("calendar/reschedule/<int:event_id>/", views.reschedule_event, name="reschedule"),
    path("calendar/skip/<int:event_id>/", views.skip_event, name="skip"),

    # bulk messaging
    path("bulk_whatsapp/", views.bulk_whatsapp, name="bulk_whatsapp"),
  path("wa-preview/", views.wa_preview_page, name="wa_preview_page"),
  path("wa-preview-csv/", views.wa_preview_csv, name="wa_preview_csv"),

    # bulk shifting
    path('<int:client_id>/reassign/', views.client_reassign_view, name='reassign'),
    path("reassign-bulk/", views.bulk_reassign_view, name="bulk_reassign"),

    # reports
    path("reports/monthly-business/", views.monthly_business_report, name="monthly_business_report"),

    # financial planner
    path("sales/financial-planner/", views.financial_planner, name="financial_planner"),
    path(
      "sales/financial-planner/download-report/",
      views.financial_planner_download_report,
      name="financial_planner_download_report",
    ),
]

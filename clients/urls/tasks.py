"""Task Management module."""
from django.urls import path

from ..views import tasks

urlpatterns = [
    path("tasks/", tasks.task_dashboard, name="task_dashboard"),
    path("tasks/my/", tasks.task_my, name="task_my"),
    path("tasks/delegated/", tasks.task_delegated, name="task_delegated"),
    path("tasks/subscribed/", tasks.task_subscribed, name="task_subscribed"),
    path("tasks/all/", tasks.task_all, name="task_all"),
    path("tasks/deleted/", tasks.task_deleted, name="task_deleted"),
    path("tasks/activities/", tasks.task_activities, name="task_activities"),
    path("tasks/categories/", tasks.task_categories, name="task_categories"),
    path("tasks/categories/create/", tasks.task_category_create, name="task_category_create"),
    path("tasks/settings/", tasks.task_settings, name="task_settings"),
    path("tasks/filters/save/", tasks.task_save_filter, name="task_save_filter"),
    path("tasks/filters/<int:filter_id>/delete/", tasks.task_delete_filter, name="task_delete_filter"),
    path("tasks/create/", tasks.task_create, name="task_create"),
    path("tasks/bulk/status/", tasks.task_bulk_status, name="task_bulk_status"),
    path("tasks/<int:pk>/", tasks.task_detail, name="task_detail"),
    path("tasks/<int:pk>/edit/", tasks.task_edit, name="task_edit"),
    path("tasks/<int:pk>/status/", tasks.task_set_status, name="task_set_status"),
    path("tasks/<int:pk>/priority/", tasks.task_set_priority, name="task_set_priority"),
    path("tasks/<int:pk>/due/", tasks.task_set_due, name="task_set_due"),
    path("tasks/<int:pk>/reschedule/", tasks.task_reschedule, name="task_reschedule"),
    path("tasks/<int:pk>/category/", tasks.task_set_category, name="task_set_category"),
    path("tasks/<int:pk>/subscriber/add/", tasks.task_add_subscriber, name="task_add_subscriber"),
    path("tasks/<int:pk>/subscriber/remove/", tasks.task_remove_subscriber, name="task_remove_subscriber"),
    path("tasks/<int:pk>/comment/", tasks.task_add_comment, name="task_add_comment"),
    path("tasks/<int:pk>/checklist/add/", tasks.task_add_checklist, name="task_add_checklist"),
    path("tasks/<int:pk>/checklist/<int:item_id>/toggle/", tasks.task_toggle_checklist, name="task_toggle_checklist"),
    path("tasks/<int:pk>/attachment/upload/", tasks.task_upload_attachment, name="task_upload_attachment"),
    path("tasks/<int:pk>/attachment/<int:att_id>/delete/", tasks.task_delete_attachment, name="task_delete_attachment"),
    path("tasks/attachment/<int:att_id>/download/", tasks.task_attachment_download, name="task_attachment_download"),
    path("tasks/<int:pk>/delete/", tasks.task_delete, name="task_delete"),
    path("tasks/<int:pk>/restore/", tasks.task_restore, name="task_restore"),
    path("tasks/<int:pk>/purge/", tasks.task_purge, name="task_purge"),
]

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, include
from sensor.resident_views import (
    my_tasks,
    request_accept_category,
    request_analyze,
    request_assign,
    request_detail,
    request_unmerge,
    requests_list,
    resident_home,
    resident_status,
    supervision,
    task_complete,
    task_start,
    worker_location,
)
from sensor import manage_views
from sensor.views import dashboard, DispatcherLoginView


admin.site.site_header = "Qaldyq әкімшілік панелі"
admin.site.site_title = "Qaldyq"
admin.site.index_title = "Басқару"


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('sensor.urls')),
    path('login/', DispatcherLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('dashboard/', dashboard, name='dashboard'),
    path('dashboard/requests/', requests_list, name='requests_list'),
    path('dashboard/requests/<int:pk>/', request_detail, name='request_detail'),
    path('dashboard/requests/<int:pk>/analyze/', request_analyze, name='request_analyze'),
    path('dashboard/requests/<int:pk>/accept-category/', request_accept_category, name='request_accept_category'),
    path('dashboard/requests/<int:pk>/unmerge/', request_unmerge, name='request_unmerge'),
    path('dashboard/requests/<int:pk>/assign/', request_assign, name='request_assign'),
    path('dashboard/supervision/', supervision, name='supervision'),
    path('manage/', manage_views.staff_list, name='manage_staff'),
    path('manage/staff/new/', manage_views.staff_create, name='manage_staff_create'),
    path('manage/staff/<int:pk>/', manage_views.staff_edit, name='manage_staff_edit'),
    path('manage/staff/<int:pk>/password/', manage_views.staff_password, name='manage_staff_password'),
    path('manage/containers/', manage_views.container_list, name='manage_containers'),
    path('manage/containers/new/', manage_views.container_create, name='manage_container_create'),
    path('manage/containers/<int:pk>/', manage_views.container_edit, name='manage_container_edit'),
    path('manage/containers/<int:pk>/delete/', manage_views.container_delete, name='manage_container_delete'),
    path('manage/system/', manage_views.system_status, name='manage_system'),
    path('tasks/', my_tasks, name='my_tasks'),
    path('tasks/location/', worker_location, name='worker_location'),
    path('tasks/<int:pk>/start/', task_start, name='task_start'),
    path('tasks/<int:pk>/complete/', task_complete, name='task_complete'),
    path('', resident_home, name='resident_home'),
    path('otinish/', resident_status, name='resident_status'),
]

# Uploaded photos; served by Django only while DEBUG is on
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

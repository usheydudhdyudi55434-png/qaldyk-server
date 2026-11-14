from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, include
from django.utils.translation import gettext_lazy as _
from django.views.i18n import JavaScriptCatalog
from sensor.resident_views import (
    my_tasks,
    request_accept_category,
    request_analyze,
    request_assign,
    request_detail,
    request_unmerge,
    requests_list,
    eco_page,
    resident_home,
    resident_status,
    supervision,
    task_complete,
    task_start,
    worker_location,
)
from sensor import manage_views
from sensor.views import dashboard, DispatcherLoginView


admin.site.site_header = _("Qaldyq әкімшілік панелі")
admin.site.site_title = "Qaldyq"
admin.site.index_title = _("Басқару")


urlpatterns = [
    path('admin/', admin.site.urls),
    # Language switch from the header, and the strings the front-end scripts need
    path('i18n/', include('django.conf.urls.i18n')),
    path('jsi18n/', JavaScriptCatalog.as_view(), name='javascript-catalog'),
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
    path('manage/eco/', manage_views.eco_overview, name='manage_eco'),
    path('manage/eco/rewards/new/', manage_views.reward_create, name='manage_reward_create'),
    path('manage/eco/rewards/<int:pk>/', manage_views.reward_edit, name='manage_reward_edit'),
    path('manage/eco/osi/new/', manage_views.osi_create, name='manage_osi_create'),
    path('manage/eco/osi/<int:pk>/', manage_views.osi_edit, name='manage_osi_edit'),
    path('manage/eco/vouchers/<int:pk>/use/', manage_views.redemption_use, name='manage_redemption_use'),
    path('tasks/', my_tasks, name='my_tasks'),
    path('tasks/location/', worker_location, name='worker_location'),
    path('tasks/<int:pk>/start/', task_start, name='task_start'),
    path('tasks/<int:pk>/complete/', task_complete, name='task_complete'),
    path('', resident_home, name='resident_home'),
    path('otinish/', resident_status, name='resident_status'),
    path('eco/', eco_page, name='eco_page'),
]

# Uploaded photos; served by Django only while DEBUG is on
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

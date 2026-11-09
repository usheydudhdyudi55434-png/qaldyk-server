from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, include
from sensor.resident_views import (
    my_tasks,
    request_detail,
    requests_list,
    resident_home,
    resident_status,
    task_complete,
    task_start,
)
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
    path('tasks/', my_tasks, name='my_tasks'),
    path('tasks/<int:pk>/start/', task_start, name='task_start'),
    path('tasks/<int:pk>/complete/', task_complete, name='task_complete'),
    path('', resident_home, name='resident_home'),
    path('otinish/', resident_status, name='resident_status'),
]

# Uploaded photos; served by Django only while DEBUG is on
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

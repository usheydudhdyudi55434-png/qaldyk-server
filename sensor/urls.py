
from django.urls import path

from .views import (
    containers_history,
    containers_overview,
    dashboard,
    sensor_data,
)


urlpatterns = [
    path('sensor/<int:container_id>/', sensor_data),
    path('containers/', containers_overview),
    path('containers/history/', containers_history),
    path('dashboard/', dashboard, name='dashboard'),
]

